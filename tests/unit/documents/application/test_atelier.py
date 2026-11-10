"""L'atelier : le CV reçu, la lettre ou l'exemple, rendus en HTML ancré (ADR-0029)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from documents.application.conftest import Env
from documents.fakes import CV_DATA, PNG_BYTES, FakeLogoFetcher, make_letter_spec, make_spec
from doubles import sample_image
from karria.modules.documents.application import RenderDocument
from karria.modules.documents.application.use_cases import (
    PHOTO_URL_CACHE_SIZE,
    _photo_urls,
    stable_photo_url,
)
from karria.modules.documents.domain.errors import (
    RenderTooLargeError,
    TemplateNotFoundError,
    TemplateNotOwnedError,
)
from karria.modules.documents.domain.templates import RENDER_MAX_HTML_BYTES
from karria.shared.errors.exceptions import InvalidInputError
from karria.shared.storage import Bucket

pytestmark = pytest.mark.unit

LETTER: dict[str, Any] = {
    "recipient": {"company": "Acme", "logo_url": "https://storage.karria.fr/logos/x/128.webp"},
    "subject": "Candidature au poste de vendeuse",
    "paragraphs": [{"kind": "libre", "text": "Un."}, {"kind": "libre", "text": "Deux."}],
    "closing": "Cordialement.",
    "sender": {"first_name": "Awa", "last_name": "Diallo"},
}


def _atelier(env: Env, logos: FakeLogoFetcher | None = None) -> RenderDocument:
    return RenderDocument(
        templates=env.templates,
        owned=env.owned,
        cvs=env.cvs,
        engine=env.engine,
        storage=env.storage,
        logos=logos or FakeLogoFetcher(),
        clock=env.clock,
    )


class TestCv:
    async def test_le_cv_recu_est_rendu_ancre_sans_lire_celui_de_la_personne(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish()
        env.engine.html = "<!doctype html><html>cv</html>"

        out = await _atelier(env).execute(env.user, template.id, cv=CV_DATA)

        assert out.html == "<!doctype html><html>cv</html>"
        assert out.pages == 1
        assert out.warnings == ()
        [call] = env.engine.called("render_html")
        assert call["anchored"] is True
        assert call["photo_url"] is None
        assert call["view"]["header"]["full_name"] == "Awa Diallo"
        assert call["view"]["paths"]["header"]["headline"] == "identity.headline"

    async def test_un_modele_premium_non_possede_est_refuse(self, env: Env) -> None:
        template, _ = env.templates.publish(premium=True)
        with pytest.raises(TemplateNotOwnedError):
            await _atelier(env).execute(env.user, template.id, cv=CV_DATA)
        env.owned.give(env.user, template.id)
        assert (await _atelier(env).execute(env.user, template.id, cv=CV_DATA)).pages == 1

    async def test_un_modele_inconnu_et_rien_a_rendre(self, env: Env) -> None:
        with pytest.raises(TemplateNotFoundError):
            await _atelier(env).execute(env.user, uuid4(), cv=CV_DATA)
        template, _ = env.templates.publish()
        with pytest.raises(InvalidInputError) as exc:
            await _atelier(env).execute(env.user, template.id)
        assert exc.value.details["reason"] == "nothing_to_render"

    async def test_les_avertissements_disent_ce_qui_manque(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(photo=True))
        env.engine.pages = 2
        cv = {**CV_DATA, "identity": {"first_name": "Awa", "last_name": "Diallo"}}

        out = await _atelier(env).execute(env.user, template.id, cv=cv)

        assert out.warnings == ("headline_missing", "photo_missing", "more_than_one_page")

    async def test_la_photo_de_la_personne_est_posee_par_une_url_signee_stable(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish(spec=make_spec(photo=True))
        env.cvs.put(env.user, image_key="cv/u/photo.jpg")
        await env.storage.put(
            Bucket.MEDIA, "cv/u/photo.jpg", sample_image(), content_type="image/jpeg"
        )
        _photo_urls.clear()

        first = await _atelier(env).execute(env.user, template.id, cv=CV_DATA)
        second = await _atelier(env).execute(env.user, template.id, cv=CV_DATA)

        assert first.warnings == ()
        urls = [c["photo_url"] for c in env.engine.called("render_html")]
        assert urls[0] == urls[1]
        assert urls[0] is not None
        assert "cv/u/photo.jpg" in urls[0]
        assert second.pages == 1

    async def test_le_cache_des_url_de_photos_est_borne(self, env: Env) -> None:
        _photo_urls.clear()
        for i in range(PHOTO_URL_CACHE_SIZE + 5):
            await stable_photo_url(env.storage, f"cv/{i}.jpg")
        assert len(_photo_urls) == PHOTO_URL_CACHE_SIZE
        _photo_urls.clear()

    async def test_un_html_trop_lourd_est_refuse(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.engine.html = "x" * (RENDER_MAX_HTML_BYTES + 1)
        with pytest.raises(RenderTooLargeError):
            await _atelier(env).execute(env.user, template.id, cv=CV_DATA)

    async def test_la_variante_ats_plafonne_l_interlettrage(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(spaced=True))
        await _atelier(env).execute(env.user, template.id, cv=CV_DATA, ats_safe=True)
        [call] = env.engine.called("render_html")
        assert call["spec"]["styles"]["section_title"]["letter_spacing_pt"] < 2.0


class TestExemple:
    async def test_l_exemple_se_rend_sans_ancre_meme_sur_un_modele_premium(self, env: Env) -> None:
        template, _ = env.templates.publish(
            premium=True,
            spec=make_spec(photo=True),
            assets={"sample_photo.png": "t/sample_photo.png", "icon.png": "t/icon.png"},
        )
        await env.storage.put(
            Bucket.TEMPLATES, "t/sample_photo.png", PNG_BYTES, content_type="image/png"
        )
        await env.storage.put(Bucket.TEMPLATES, "t/icon.png", PNG_BYTES, content_type="image/png")

        out = await _atelier(env).execute(env.user, template.id, sample=True)

        [call] = env.engine.called("render_html")
        assert call["anchored"] is False
        assert call["view"]["sample"] is True
        assert call["photo_url"] == "photo"
        assert set(call["assets"]) == {"photo", "icon.png"}
        assert out.warnings == ()


class TestLettre:
    async def test_la_lettre_est_rendue_avec_son_logo_converti_en_png(self, env: Env) -> None:
        template, _ = env.templates.publish()
        logos = FakeLogoFetcher(logos={LETTER["recipient"]["logo_url"]: sample_image()})

        out = await _atelier(env, logos).execute(env.user, template.id, letter=LETTER)

        [call] = env.engine.called("render_html")
        assert call["view"]["kind"] == "letter"
        assert call["assets"]["logo.png"].startswith(b"\x89PNG")
        assert out.warnings == ()
        assert logos.asked == [LETTER["recipient"]["logo_url"]]

    async def test_un_logo_injoignable_ou_illisible_vaut_un_avertissement(self, env: Env) -> None:
        template, _ = env.templates.publish()

        missing = await _atelier(env).execute(env.user, template.id, letter=LETTER)
        broken = FakeLogoFetcher(logos={LETTER["recipient"]["logo_url"]: b"pas une image"})
        unreadable = await _atelier(env, broken).execute(env.user, template.id, letter=LETTER)

        assert missing.warnings == ("logo_unavailable",)
        assert unreadable.warnings == ("logo_unavailable",)
        assert "logo.png" not in env.engine.called("render_html")[0]["assets"]

    async def test_les_ecarts_aux_usages_sont_signales(self, env: Env) -> None:
        template, _ = env.templates.publish()
        letter = {**LETTER, "recipient": {"company": ""}, "subject": "Bonjour", "paragraphs": []}

        out = await _atelier(env).execute(env.user, template.id, letter=letter)

        assert out.warnings == ("too_few_paragraphs", "subject_not_explicit", "company_missing")

    async def test_la_lettre_prime_sur_le_cv_quand_les_deux_sont_donnes(self, env: Env) -> None:
        template, _ = env.templates.publish()
        await _atelier(env).execute(env.user, template.id, cv=CV_DATA, letter=LETTER)
        assert env.engine.called("render_html")[0]["view"]["kind"] == "letter"
        assert replace(template, label="x").label == "x"


class TestCouleurPrincipale:
    """ADR-0034 : la couleur choisie habille le décor du modèle avant le rendu ; le texte reste."""

    async def test_la_couleur_choisie_recolore_le_decor_de_la_specification_rendue(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish()

        await _atelier(env).execute(env.user, template.id, cv=CV_DATA, primary_color="#7B1E2B")

        [call] = env.engine.called("render_html")
        assert call["spec"]["page_decorations"][0]["stroke"] == "#7B1E2B"
        assert call["spec"]["styles"]["section_title"]["color"] == "#1A5276", (
            "un titre est un texte"
        )
        assert call["spec"]["styles"]["name"]["color"] != "#7B1E2B", "le nom n'est pas en bordeaux"

    async def test_sans_couleur_ou_avec_celle_d_origine_la_specification_est_celle_du_modele(
        self, env: Env
    ) -> None:
        template, version = env.templates.publish()

        await _atelier(env).execute(env.user, template.id, cv=CV_DATA)
        await _atelier(env).execute(env.user, template.id, cv=CV_DATA, primary_color="#1a5276")

        for call in env.engine.called("render_html"):
            assert call["spec"] == version.spec

    async def test_la_couleur_s_applique_apres_la_variante_ats_et_a_la_lettre_assortie(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish(
            spec=make_spec(spaced=True), letter_spec=make_letter_spec(accent="#1A5276")
        )

        await _atelier(env).execute(
            env.user, template.id, cv=CV_DATA, ats_safe=True, primary_color="#2E7D32"
        )
        letter = await _atelier(env).execute(
            env.user, template.id, letter=LETTER, primary_color="#2E7D32"
        )

        cv_call, letter_call = env.engine.called("render_html")
        title = cv_call["spec"]["styles"]["section_title"]
        assert cv_call["spec"]["page_decorations"][0]["stroke"] == "#2E7D32"
        assert title["letter_spacing_pt"] < 2.0, "l'interlettrage plafonné reste plafonné"
        assert letter.letter_style == "template"
        # La lettre assortie suit : son objet, qui avait la couleur du décor du CV,
        # prend la couleur choisie ; le texte courant reste.
        letter_styles = letter_call["spec"]["styles"]
        assert letter_styles["subject"]["color"] == "#2E7D32"
        assert letter_styles["paragraph"]["color"] == "#111111"
        assert "section_title" not in letter_styles

    async def test_sans_lettre_assortie_la_lettre_sobre_reste_neutre(self, env: Env) -> None:
        template, _ = env.templates.publish()

        out = await _atelier(env).execute(
            env.user, template.id, letter=LETTER, primary_color="#2E7D32"
        )

        assert out.letter_style == "plain"
        [call] = env.engine.called("render_html")
        assert "accent" not in call["spec"]
        assert call["spec"]["styles"]["subject"]["color"] == "#111111"
        assert call["spec"]["styles"]["paragraph"]["color"] == "#111111"
