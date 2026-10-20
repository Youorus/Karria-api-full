"""Le socle image : ce qu'il reconnaît, redresse, allège et refuse (ADR-0009).

Ce que ces tests protègent, dans l'ordre :

1. **rien ne passe sans être reconnu** — le type vient des octets, jamais
   de l'étiquette, et ce qui n'est pas une image est refusé ;
2. **une image trop grande devient une image qui tient** — dimensions
   puis octets, sous le plafond, sans exception ;
3. **les métadonnées partent** — l'EXIF, GPS compris, ne survit pas ;
4. **une image conforme reste intacte** — on ne dégrade pas ce qui n'a
   pas besoin de l'être ;
5. **le HEIC entre** — c'est ce que produit un iPhone.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from doubles import sample_image
from karria.shared.images import (
    ImageCannotFitError,
    ImagePolicy,
    NotAnImageError,
    normalize_image,
    sniff_content_type,
)

pytestmark = pytest.mark.unit

PHOTOS = ImagePolicy(max_edge=2_048, quality=85)
DOCUMENTS = ImagePolicy(max_edge=3_000, quality=92, min_quality=80)
UN_MEGA = 1024 * 1024


def _ouvrir(payload: bytes) -> Image.Image:
    return Image.open(BytesIO(payload))


class TestReconnaissance:
    def test_le_type_vient_des_octets(self) -> None:
        assert sniff_content_type(sample_image(fmt="JPEG")) == "image/jpeg"
        assert sniff_content_type(sample_image(fmt="PNG")) == "image/png"
        assert sniff_content_type(sample_image(fmt="WEBP")) == "image/webp"

    def test_ce_qui_n_est_pas_une_image_est_reconnu_comme_tel(self) -> None:
        assert sniff_content_type(b"%PDF-1.4 un document") is None
        assert sniff_content_type(b"\xff\xd8\xff\xe0 des octets qui font semblant") is None
        assert sniff_content_type(b"") is None

    def test_des_octets_qui_font_semblant_sont_refuses(self) -> None:
        with pytest.raises(NotAnImageError):
            normalize_image(b"\xff\xd8\xff\xe0 pas une image", PHOTOS, max_bytes=UN_MEGA)


class TestReduction:
    def test_une_grande_image_est_ramenee_a_la_dimension_de_la_politique(self) -> None:
        resultat = normalize_image(
            sample_image(4_000, 3_000, fmt="PNG"), PHOTOS, max_bytes=5 * UN_MEGA
        )
        assert resultat.content_type == "image/jpeg"
        assert (resultat.width, resultat.height) == (2_048, 1_536)
        assert len(resultat.payload) <= 5 * UN_MEGA
        assert not resultat.untouched

    def test_une_image_est_recompressee_jusqu_a_tenir_sous_le_plafond(self) -> None:
        """Un plafond serré force la descente : qualité d'abord, dimensions ensuite."""
        photo = sample_image(2_000, 2_000)
        # Sous ce plafond, baisser la qualité suffit : les dimensions
        # ne bougent pas — c'est la règle, la qualité d'abord.
        resultat = normalize_image(photo, PHOTOS, max_bytes=700_000)
        assert len(resultat.payload) <= 700_000 < len(photo)
        assert resultat.content_type == "image/jpeg"
        assert not resultat.untouched
        assert (resultat.width, resultat.height) == (2_000, 2_000)

    def test_les_dimensions_baissent_quand_la_qualite_plancher_ne_suffit_plus(self) -> None:
        # Sous ce plafond, la qualité plancher ne suffit plus à 2 000 px :
        # l'image doit être réduite pour tenir.
        resultat = normalize_image(sample_image(2_000, 2_000), PHOTOS, max_bytes=90_000)
        assert len(resultat.payload) <= 90_000
        assert resultat.width < 2_000

    def test_un_plafond_impossible_est_refuse_plutot_que_contourne(self) -> None:
        with pytest.raises(ImageCannotFitError):
            normalize_image(sample_image(2_000, 2_000), PHOTOS, max_bytes=500)

    def test_les_proportions_sont_conservees(self) -> None:
        resultat = normalize_image(sample_image(3_000, 1_000), PHOTOS, max_bytes=5 * UN_MEGA)
        assert (resultat.width, resultat.height) == (2_048, 683)


class TestMetadonnees:
    def test_l_orientation_exif_est_appliquee_et_l_exif_retire(self) -> None:
        """Orientation 6 : l'image est tournée de 90°. Largeur et hauteur s'échangent."""
        photo = sample_image(400, 200, exif_orientation=6)
        assert _ouvrir(photo).getexif()

        resultat = normalize_image(photo, PHOTOS, max_bytes=UN_MEGA)

        assert (resultat.width, resultat.height) == (200, 400)
        assert not _ouvrir(resultat.payload).getexif()

    def test_une_image_conforme_reste_intacte(self) -> None:
        """Pas d'EXIF, dans les dimensions, sous le plafond : les mêmes octets."""
        photo = sample_image(640, 480)
        resultat = normalize_image(photo, PHOTOS, max_bytes=UN_MEGA)
        assert resultat.untouched
        assert resultat.payload is photo
        assert resultat.content_type == "image/jpeg"


class TestFormats:
    def test_un_png_sans_transparence_devient_un_jpeg(self) -> None:
        resultat = normalize_image(sample_image(300, 200, fmt="PNG"), PHOTOS, max_bytes=UN_MEGA)
        assert resultat.content_type == "image/jpeg"

    def test_la_transparence_est_gardee_quand_la_politique_le_demande(self) -> None:
        politique = ImagePolicy(max_edge=2_048, keep_alpha=True)
        resultat = normalize_image(
            sample_image(300, 200, fmt="PNG", alpha=True), politique, max_bytes=UN_MEGA
        )
        assert resultat.content_type == "image/png"
        assert _ouvrir(resultat.payload).mode == "RGBA"

    def test_la_transparence_est_aplatie_sur_du_blanc_sinon(self) -> None:
        resultat = normalize_image(
            sample_image(300, 200, fmt="PNG", alpha=True), PHOTOS, max_bytes=UN_MEGA
        )
        assert resultat.content_type == "image/jpeg"
        assert _ouvrir(resultat.payload).mode == "RGB"

    def test_un_webp_est_converti(self) -> None:
        resultat = normalize_image(sample_image(300, 200, fmt="WEBP"), PHOTOS, max_bytes=UN_MEGA)
        assert resultat.content_type == "image/jpeg"

    def test_un_heic_d_iphone_entre_et_ressort_en_jpeg(self) -> None:
        """Le format que produit un iPhone récent, écrit avec pillow-heif."""
        source = _ouvrir(sample_image(500, 300))
        buffer = BytesIO()
        source.save(buffer, format="HEIF", quality=80)
        heic = buffer.getvalue()
        assert sniff_content_type(heic) == "image/heic"

        resultat = normalize_image(heic, PHOTOS, max_bytes=UN_MEGA)

        assert resultat.content_type == "image/jpeg"
        assert (resultat.width, resultat.height) == (500, 300)


class TestPieceDIdentite:
    def test_une_piece_garde_une_qualite_haute(self) -> None:
        """Le plancher des documents est à 80 : on ne descend pas en dessous."""
        piece = sample_image(3_500, 2_400)
        resultat = normalize_image(piece, DOCUMENTS, max_bytes=10 * UN_MEGA)
        assert (resultat.width, resultat.height) == (3_000, 2_057)
        # La même image sous la politique des photos pèse nettement
        # moins : la preuve que la qualité des documents n'a pas été
        # écrasée pour gagner des octets.
        en_photo = normalize_image(
            piece, ImagePolicy(max_edge=3_000, quality=60), max_bytes=10 * UN_MEGA
        )
        assert len(resultat.payload) > len(en_photo.payload) * 1.3
