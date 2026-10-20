"""Construction et validation des clés objet.

Ces règles décident de ce qui finit dans les URL, les journaux d'accès du
fournisseur et les sauvegardes. Trois d'entre elles corrigent un défaut
mesuré en production sur la V1 `karria_api`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from karria.shared.storage import (
    KEY_MAX_LENGTH,
    InvalidObjectKeyError,
    build_key,
    ensure_key_under,
    extension_for,
    is_valid_key,
)

pytestmark = pytest.mark.unit

INSTANT = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class TestConstruction:
    def test_range_par_segment_puis_par_jour(self) -> None:
        key = build_key("users", "abc", "avatar", extension="jpg", at=INSTANT)

        assert key.startswith("users/abc/avatar/2026-08-09/")
        assert key.endswith(".jpg")

    def test_deux_appels_ne_produisent_jamais_la_meme_cle(self) -> None:
        """C'est ce qui rend toute écriture naturellement non destructive."""
        premier = build_key("users", "abc", extension="jpg", at=INSTANT)
        second = build_key("users", "abc", extension="jpg", at=INSTANT)

        assert premier != second

    def test_lidentifiant_final_est_opaque(self) -> None:
        """Aucune information n'est déductible du nom du fichier lui-même."""
        key = build_key("users", "abc", extension="png", at=INSTANT)

        nom = key.rsplit("/", 1)[-1].removesuffix(".png")
        assert len(nom) == 32
        assert nom.isalnum()

    @pytest.mark.parametrize(
        ("segment", "attendu"),
        [
            ("Users", "users"),
            ("mon dossier", "mon-dossier"),
            ("Photos/2026", "photos-2026"),
            ("--bords--", "bords"),
            (42, "42"),
        ],
    )
    def test_les_segments_sont_ramenes_a_un_alphabet_sur(
        self, segment: str | int, attendu: str
    ) -> None:
        key = build_key(segment, extension="jpg", at=INSTANT)

        assert key.split("/")[0] == attendu

    def test_lextension_est_normalisee(self) -> None:
        assert build_key("a", extension=".JPG", at=INSTANT).endswith(".jpg")

    def test_un_segment_vide_apres_nettoyage_est_refuse(self) -> None:
        with pytest.raises(InvalidObjectKeyError):
            build_key("///", extension="jpg", at=INSTANT)

    def test_une_cle_trop_longue_est_refusee(self) -> None:
        with pytest.raises(InvalidObjectKeyError):
            build_key("a" * (KEY_MAX_LENGTH + 10), extension="jpg", at=INSTANT)


class TestAbsenceDeDonneePersonnelle:
    """Le piège n°2 de la V1 : `users/42_jane.doe-example.com/...`.

    L'e-mail s'y retrouve dans les URL, les journaux d'accès du fournisseur
    et les sauvegardes — et devient impossible à rectifier le jour où la
    personne en change.
    """

    def test_une_arobase_ne_survit_pas_a_la_construction(self) -> None:
        key = build_key("users", "jane.doe@example.com", extension="jpg", at=INSTANT)

        assert "@" not in key

    def test_un_identifiant_opaque_traverse_intact(self) -> None:
        """C'est bien ce qu'on veut y mettre : un UUID, et rien d'autre."""
        user_id = uuid4()

        key = build_key("users", str(user_id), "avatar", extension="jpg", at=INSTANT)

        assert str(user_id) in key


class TestValidation:
    @pytest.mark.parametrize(
        "key",
        [
            "users/abc/avatar/2026-08-09/f3c2.jpg",
            "documents/a-b/c.pdf",
            "a1",
        ],
    )
    def test_accepte_une_cle_bien_formee(self, key: str) -> None:
        assert is_valid_key(key)

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "/users/abc",  # barre initiale
            "users//abc",  # barre doublée
            "users/../etc/passwd",  # traversée de chemin
            "users/abc/",  # barre finale
            "Users/ABC",  # majuscules
            "users/abc file.jpg",  # espace
            "users/abc\\n.jpg",  # saut de ligne
        ],
    )
    def test_refuse_une_cle_douteuse(self, key: str) -> None:
        assert not is_valid_key(key)

    def test_une_cle_sous_le_prefixe_attendu_passe(self) -> None:
        key = "users/abc/avatar/2026-08-09/f3c2.jpg"

        assert ensure_key_under(key, "users/abc/avatar") == key

    def test_le_prefixe_tolere_labsence_de_barre_finale(self) -> None:
        key = "users/abc/avatar/x.jpg"

        assert ensure_key_under(key, "users/abc/avatar/") == key

    def test_une_cle_dun_autre_compte_est_refusee(self) -> None:
        """Le contrôle qui rend l'envoi direct sûr.

        Le serveur ne voit jamais passer les octets : la clé est tout ce
        dont il dispose pour ne pas attribuer à quelqu'un le fichier d'un
        autre.
        """
        with pytest.raises(InvalidObjectKeyError):
            ensure_key_under("users/quelquun-dautre/avatar/x.jpg", "users/abc/avatar")

    def test_un_prefixe_partiel_ne_suffit_pas(self) -> None:
        """`users/abc` ne doit pas ouvrir `users/abcdef`."""
        with pytest.raises(InvalidObjectKeyError):
            ensure_key_under("users/abcdef/avatar/x.jpg", "users/abc")

    def test_une_traversee_de_chemin_est_refusee(self) -> None:
        with pytest.raises(InvalidObjectKeyError):
            ensure_key_under("users/abc/avatar/../../autre/x.jpg", "users/abc/avatar")


class TestExtensions:
    @pytest.mark.parametrize(
        ("content_type", "extension"),
        [
            ("image/jpeg", "jpg"),
            ("image/png", "png"),
            ("image/webp", "webp"),
            ("application/pdf", "pdf"),
            ("application/inconnu", "bin"),
        ],
    )
    def test_la_table_est_explicite_et_stable(self, content_type: str, extension: str) -> None:
        """`mimetypes.guess_extension` varie selon le système hôte.

        Il rend `image/jpeg` tantôt `.jpg`, tantôt `.jpe` : une clé dont
        l'extension dépend de la machine qui l'a écrite est une clé qu'on ne
        saura pas reconstituer.
        """
        assert extension_for(content_type) == extension
