"""La couleur du décor au rendu : pictogrammes teintés, couleur de galerie remesurée (ADR-0034 § 2026-11-06)."""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from documents.application.conftest import Env
from documents.fakes import CV_DATA, make_spec, make_template, make_version
from karria.modules.documents.application import RefreshTemplateColors, tint_icons
from karria.modules.documents.application.use_cases import _tinted_icons
from karria.modules.documents.domain.templates import VersionStatus
from karria.shared.images import single_ink
from karria.shared.storage import Bucket

pytestmark = pytest.mark.unit

BLEU = "#1A5276"
BORDEAUX = "#7B1E2B"


def pictogram(color: str, *, background: str | None = "#FFFFFF") -> bytes:
    """Un rond de la couleur, anticrénelé, sur du blanc ou sur du transparent."""
    size = 40
    big = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(big).ellipse((8, 8, size * 4 - 8, size * 4 - 8), fill=255)
    mask = big.resize((size, size), Image.Resampling.LANCZOS)
    if background is None:
        image = Image.new("RGBA", (size, size), color)
        image.putalpha(mask)
    else:
        image = Image.new("RGB", (size, size), background)
        image.paste(Image.new("RGB", (size, size), color), (0, 0), mask)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    _tinted_icons.clear()


class TestPictogrammesTeintes:
    def test_un_pictogramme_a_la_couleur_du_decor_prend_la_couleur_choisie(self) -> None:
        assets = {"icon_phone.png": pictogram(BLEU), "sample_photo.png": b"\x89PNG photo"}

        out = tint_icons(assets, make_spec(), BORDEAUX)

        assert out["sample_photo.png"] == assets["sample_photo.png"], "seuls les pictogrammes"
        assert out["icon_phone.png"] != assets["icon_phone.png"]
        assert single_ink(out["icon_phone.png"]) == BORDEAUX

    def test_un_pictogramme_d_une_autre_couleur_ou_bicolore_reste(self) -> None:
        autre = pictogram("#C0392B")
        bicolore = pictogram("#C0392B")
        image = Image.open(BytesIO(bicolore)).convert("RGB")
        ImageDraw.Draw(image).rectangle((0, 0, 10, 10), fill="#2E7D32")
        buffer = BytesIO()
        image.save(buffer, "PNG")

        out = tint_icons(
            {"icon_a.png": autre, "icon_b.png": buffer.getvalue()}, make_spec(), BORDEAUX
        )

        assert out == {"icon_a.png": autre, "icon_b.png": buffer.getvalue()}

    def test_sans_couleur_ou_avec_celle_d_origine_rien_n_est_teinte(self) -> None:
        assets = {"icon_phone.png": pictogram(BLEU)}
        spec_sans_decor = {**make_spec(), "page_decorations": []}

        assert tint_icons(assets, make_spec(), None) is assets
        assert tint_icons(assets, make_spec(), "#1a5276") is assets
        assert tint_icons(assets, spec_sans_decor, BORDEAUX) is assets

    def test_la_teinte_est_calculee_une_fois_et_gardee(self) -> None:
        assets = {"icon_phone.png": pictogram(BLEU)}

        first = tint_icons(assets, make_spec(), BORDEAUX)
        second = tint_icons(assets, make_spec(), BORDEAUX)

        assert second["icon_phone.png"] is first["icon_phone.png"]
        assert len(_tinted_icons) == 1

    def test_le_cache_est_borne(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "karria.modules.documents.application.use_cases.ICON_TINT_CACHE_SIZE", 1
        )

        tint_icons({"icon_a.png": pictogram(BLEU)}, make_spec(), BORDEAUX)
        tint_icons({"icon_b.png": pictogram(BLEU, background=None)}, make_spec(), BORDEAUX)

        assert len(_tinted_icons) == 1


class TestPictogrammesAuRendu:
    async def test_l_apercu_et_l_atelier_envoient_les_pictogrammes_teintes(self, env: Env) -> None:
        template, _ = env.templates.publish(assets={"icon_phone.png": "icons/phone.png"})
        await env.storage.put(
            Bucket.TEMPLATES, "icons/phone.png", pictogram(BLEU), content_type="image/png"
        )
        env.cvs.put(env.user)

        *_, assets = await env.renderer().prepare(
            env.user, template.id, ats_safe=False, primary_color=BORDEAUX
        )
        assert single_ink(assets["icon_phone.png"]) == BORDEAUX

        *_, assets = await env.renderer().prepare(env.user, template.id, ats_safe=False)
        assert single_ink(assets["icon_phone.png"]) == BLEU

    async def test_l_atelier_teinte_le_cv_recu_et_l_exemple(self, env: Env) -> None:
        from documents.application.test_atelier import _atelier  # noqa: PLC0415 - même montage

        template, _ = env.templates.publish(assets={"icon_phone.png": "icons/phone.png"})
        await env.storage.put(
            Bucket.TEMPLATES, "icons/phone.png", pictogram(BLEU), content_type="image/png"
        )

        await _atelier(env).execute(env.user, template.id, cv=CV_DATA, primary_color=BORDEAUX)
        await _atelier(env).execute(env.user, template.id, sample=True, primary_color=BORDEAUX)

        for call in env.engine.called("render_html"):
            assert single_ink(call["assets"]["icon_phone.png"]) == BORDEAUX


class TestCouleurExpliciteEtChoix:
    async def test_une_couleur_explicite_prime_sur_celle_du_choix(self, env: Env) -> None:
        from karria.modules.documents.domain.templates import Selection  # noqa: PLC0415

        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        await env.owned.save_selection(
            Selection(
                user_id=env.user,
                template_id=template.id,
                ats_safe=False,
                updated_at=env.clock.now(),
                primary_color="#2E7D32",
            )
        )

        generated = await env.export().execute(
            env.user,
            template_id=None,
            formats=["pdf"],
            ats_safe=None,
            offer_id=None,
            primary_color=BORDEAUX,
        )

        assert generated.primary_color == BORDEAUX
        assert env.engine.called("render_pdf")[0]["spec"]["page_decorations"][0]["stroke"] == (
            BORDEAUX
        )


class TestCouleurDeGalerieRemesuree:
    async def test_les_modeles_publies_prennent_la_couleur_de_leur_decor(self, env: Env) -> None:
        template, _ = env.templates.publish()
        await env.templates.save(replace(template, primary_color="#000000"))
        brouillon = make_template(name="brouillon")
        await env.templates.save(brouillon)
        sans_spec = make_template(name="sans-spec")
        version = make_version(sans_spec.id, spec=None, status=VersionStatus.PUBLISHED)
        await env.templates.save_version(version)
        await env.templates.save(replace(sans_spec, published_version_id=version.id))

        changes = await RefreshTemplateColors(env.templates, env.clock).execute()

        assert [(t.id, c) for t, c in changes] == [(template.id, BLEU)]
        assert env.templates.templates[template.id].primary_color == BLEU
        assert env.templates.templates[brouillon.id].primary_color is None

    async def test_un_modele_a_jour_n_est_pas_reecrit_et_la_simulation_n_ecrit_rien(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish()
        await env.templates.save(replace(template, primary_color=BLEU))
        other, _ = env.templates.publish(name="autre")
        await env.templates.save(replace(other, primary_color="#000000"))

        simulated = await RefreshTemplateColors(env.templates, env.clock).execute(apply=False)

        assert [t.id for t, _ in simulated] == [other.id]
        assert env.templates.templates[other.id].primary_color == "#000000"
