"""Aperçu et export : le CV de la personne dans un modèle, ses fichiers et son score ATS."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from documents.application.conftest import Env
from documents.fakes import (
    CLEAN_TEXT,
    CV_DATA,
    DOCX_BYTES,
    NOW,
    PDF_BYTES,
    PNG_BYTES,
    SPACED_TEXT,
    make_spec,
)
from karria.modules.documents.domain.errors import (
    CvMissingError,
    InvalidFormatError,
    TailoringNotAvailableError,
    TemplateLockedError,
    TemplateNotFoundError,
)
from karria.modules.documents.domain.templates import ExplanationStatus, Fit, Selection
from karria.shared.storage import Bucket

pytestmark = pytest.mark.unit


class TestPreparation:
    async def test_un_modele_pro_non_possede_ne_se_rend_pas(self, env: Env) -> None:
        template, _ = env.templates.publish(premium=True)
        env.cvs.put(env.user)

        with pytest.raises(TemplateLockedError):
            await env.renderer().prepare(env.user, template.id, ats_safe=False)

    async def test_un_modele_pro_possede_se_rend(self, env: Env) -> None:
        template, _ = env.templates.publish(premium=True)
        env.owned.give(env.user, template.id)
        env.cvs.put(env.user)

        found, *_ = await env.renderer().prepare(env.user, template.id, ats_safe=False)

        assert found.id == template.id

    async def test_sans_cv_finalise_rien_ne_se_rend(self, env: Env) -> None:
        template, _ = env.templates.publish()

        with pytest.raises(CvMissingError):
            await env.renderer().prepare(env.user, template.id, ats_safe=False)

    async def test_la_variante_ats_plafonne_l_interlettrage_sans_toucher_la_version(
        self, env: Env
    ) -> None:
        template, version = env.templates.publish(spec=make_spec(spaced=True))
        env.cvs.put(env.user)

        _, _, spec, *_ = await env.renderer().prepare(env.user, template.id, ats_safe=True)

        assert spec["styles"]["section_title"]["letter_spacing_pt"] == 0.8
        assert version.spec is not None
        assert version.spec["styles"]["section_title"]["letter_spacing_pt"] == 2.0

    async def test_la_photo_du_cv_est_lue_quand_le_modele_en_a_une(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(photo=True))
        env.cvs.put(env.user, image_key="cv/photo.png")
        await env.storage.put(Bucket.MEDIA, "cv/photo.png", PNG_BYTES, content_type="image/png")

        *_, photo, _ = await env.renderer().prepare(env.user, template.id, ats_safe=False)

        assert photo == PNG_BYTES

    async def test_une_photo_disparue_ne_bloque_pas_le_rendu(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(photo=True))
        env.cvs.put(env.user, image_key="cv/absente.png")

        *_, photo, _ = await env.renderer().prepare(env.user, template.id, ats_safe=False)

        assert photo is None

    async def test_sans_emplacement_photo_la_photo_du_cv_n_est_pas_lue(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(photo=False))
        env.cvs.put(env.user, image_key="cv/absente.png")

        *_, photo, _ = await env.renderer().prepare(env.user, template.id, ats_safe=False)

        assert photo is None

    async def test_les_pictogrammes_sont_lus_mais_pas_la_photo_d_exemple(self, env: Env) -> None:
        template, version = env.templates.publish(
            assets={"icon.png": "t/icon.png", "sample_photo.png": "t/sample_photo.png"}
        )
        await env.storage.put(Bucket.TEMPLATES, "t/icon.png", PNG_BYTES, content_type="image/png")
        env.cvs.put(env.user)

        *_, assets = await env.renderer().prepare(env.user, template.id, ats_safe=False)

        assert assets == {"icon.png": PNG_BYTES}
        assert "sample_photo.png" in version.assets


class TestApercu:
    async def test_l_apercu_rend_les_pages_et_les_met_en_cache(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        env.engine.pages = 2

        pages, fit = await env.preview().execute(env.user, template.id, ats_safe=False)

        assert fit == env.engine.fit
        assert [p["width"] for p in pages] == [1240, 1240]
        for page in pages:
            assert page["key"].startswith(f"previews/{env.user}/")
            assert await env.storage.get(Bucket.TEMP, page["key"]) == PNG_BYTES
        cached = [
            k for (b, k) in env.storage.objects if b is Bucket.TEMP and k.endswith("fit.json")
        ]
        assert len(cached) == 1
        stored = json.loads(await env.storage.get(Bucket.TEMP, cached[0]))
        assert stored["pages"] == pages

    async def test_un_second_apercu_identique_sort_du_cache_sans_rendu(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        first = await env.preview().execute(env.user, template.id, ats_safe=False)
        renders = len(env.engine.called("render_pdf"))

        second = await env.preview().execute(env.user, template.id, ats_safe=False)

        assert second == first
        assert isinstance(second[1], Fit)
        assert len(env.engine.called("render_pdf")) == renders

    async def test_la_variante_ats_a_son_propre_cache(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        await env.preview().execute(env.user, template.id, ats_safe=False)

        await env.preview().execute(env.user, template.id, ats_safe=True)

        assert len(env.engine.called("render_pdf")) == 2


class TestExport:
    async def test_l_export_par_defaut_rend_pdf_et_docx_ranges_dans_documents(
        self, env: Env
    ) -> None:
        template, version = env.templates.publish()
        env.cvs.put(env.user)

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=None, ats_safe=None, offer_id=None
        )

        prefix = f"generated-cvs/{env.user}/{generated.id}"
        assert generated.pdf_key == f"{prefix}/cv.pdf"
        assert generated.docx_key == f"{prefix}/cv.docx"
        assert await env.storage.get(Bucket.DOCUMENTS, generated.pdf_key) == PDF_BYTES
        assert await env.storage.get(Bucket.DOCUMENTS, generated.docx_key) == DOCX_BYTES
        assert generated.template_version_id == version.id
        assert generated.template_label == template.label
        assert generated.ats_safe is False
        assert generated.created_at == NOW
        assert generated.full_name == "Awa Diallo"
        assert generated.fit == env.engine.fit
        assert generated.ats["format"] == "pdf"
        assert generated.ats_docx is not None
        assert generated.ats_docx["format"] == "docx"
        assert generated.explanation_status is ExplanationStatus.PENDING
        assert env.generated.items[generated.id] == generated
        assert env.templates.templates[template.id].uses_count == 1
        assert env.jobs.explanations == [generated.id]
        assert env.engine.called("render_docx")[0]["fit"] == env.engine.fit

    async def test_l_export_pdf_seul_ne_rend_pas_de_docx(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=False, offer_id=None
        )

        assert generated.docx_key is None
        assert generated.ats_docx is None
        assert generated.ats["format"] == "pdf"
        assert env.engine.called("render_docx") == []

    async def test_l_export_docx_seul_prend_le_score_du_docx(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["docx"], ats_safe=False, offer_id=None
        )

        assert generated.pdf_key is None
        assert generated.docx_key is not None
        assert generated.ats["format"] == "docx"
        assert not any(b is Bucket.DOCUMENTS and k.endswith(".pdf") for b, k in env.storage.objects)

    async def test_un_format_inconnu_est_refuse(self, env: Env) -> None:
        with pytest.raises(InvalidFormatError):
            await env.export().execute(
                env.user, template_id=uuid4(), formats=["odt"], ats_safe=None, offer_id=None
            )

    async def test_l_adaptation_a_une_offre_n_est_pas_encore_servie(self, env: Env) -> None:
        with pytest.raises(TailoringNotAvailableError):
            await env.export().execute(
                env.user, template_id=None, formats=None, ats_safe=None, offer_id=uuid4()
            )

    async def test_sans_modele_l_export_prend_le_modele_choisi_et_sa_variante(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        await env.owned.save_selection(
            Selection(user_id=env.user, template_id=template.id, ats_safe=True, updated_at=NOW)
        )

        generated = await env.export().execute(
            env.user, template_id=None, formats=["pdf"], ats_safe=None, offer_id=None
        )

        assert generated.template_id == template.id
        assert generated.ats_safe is True

    async def test_la_variante_demandee_l_emporte_sur_celle_du_choix(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        await env.owned.save_selection(
            Selection(user_id=env.user, template_id=template.id, ats_safe=True, updated_at=NOW)
        )

        generated = await env.export().execute(
            env.user, template_id=None, formats=["pdf"], ats_safe=False, offer_id=None
        )

        assert generated.ats_safe is False

    async def test_sans_modele_ni_choix_l_export_est_refuse(self, env: Env) -> None:
        with pytest.raises(TemplateNotFoundError):
            await env.export().execute(
                env.user, template_id=None, formats=None, ats_safe=None, offer_id=None
            )

    async def test_un_modele_pro_non_possede_ne_s_exporte_pas(self, env: Env) -> None:
        template, _ = env.templates.publish(premium=True)
        env.cvs.put(env.user)

        with pytest.raises(TemplateLockedError):
            await env.export().execute(
                env.user, template_id=template.id, formats=None, ats_safe=None, offer_id=None
            )

    async def test_sans_cv_l_export_est_refuse(self, env: Env) -> None:
        template, _ = env.templates.publish()

        with pytest.raises(CvMissingError):
            await env.export().execute(
                env.user, template_id=template.id, formats=None, ats_safe=None, offer_id=None
            )

    async def test_des_intitules_lus_lettre_a_lettre_suggerent_la_variante_ats(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish(spec=make_spec(spaced=True))
        env.cvs.put(env.user)
        env.engine.texts = {"pdf": SPACED_TEXT, "docx": SPACED_TEXT}
        env.engine.texts_safe = {"pdf": CLEAN_TEXT, "docx": CLEAN_TEXT}

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=None, ats_safe=False, offer_id=None
        )

        assert generated.ats["suggest_ats_safe"] is True
        assert generated.ats_docx is not None
        assert generated.ats_docx["suggest_ats_safe"] is True
        # La variante a été rendue et notée, pas seulement supposée.
        assert sum(1 for name, _ in env.engine.calls if name == "render_pdf") == 2

    async def test_la_variante_ats_n_est_pas_proposee_si_elle_ne_gagne_rien(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(spaced=True))
        env.cvs.put(env.user)
        env.engine.texts = {"pdf": SPACED_TEXT, "docx": SPACED_TEXT}
        env.engine.texts_safe = {"pdf": SPACED_TEXT, "docx": SPACED_TEXT}

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=None, ats_safe=False, offer_id=None
        )

        assert generated.ats["suggest_ats_safe"] is False

    async def test_la_variante_ats_deja_active_n_est_plus_suggeree(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(spaced=True))
        env.cvs.put(env.user)
        env.engine.texts = {"pdf": SPACED_TEXT, "docx": SPACED_TEXT}

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=True, offer_id=None
        )

        assert generated.ats["suggest_ats_safe"] is False

    async def test_un_modele_sans_interlettrage_ne_suggere_rien(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(spaced=False))
        env.cvs.put(env.user)
        env.engine.texts = {"pdf": SPACED_TEXT, "docx": SPACED_TEXT}

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=False, offer_id=None
        )

        assert generated.ats["suggest_ats_safe"] is False

    async def test_un_cv_sans_identite_ni_bande_s_exporte_sans_nom(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(bands=False))
        env.cvs.put(env.user, {"summary": "Profil seul."})

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=False, offer_id=None
        )

        assert generated.full_name == ""


class TestCvRecu:
    """Un dossier de candidature exporte sa copie adaptée, pas le CV de base (ADR-0033)."""

    async def test_le_cv_recu_remplace_le_cv_de_base_et_garde_sa_photo(self, env: Env) -> None:
        template, _ = env.templates.publish(spec=make_spec(photo=True))
        env.cvs.put(env.user, image_key="cv/u/photo.jpg")
        await env.storage.put(
            Bucket.MEDIA, "cv/u/photo.jpg", b"\xff\xd8photo", content_type="image/jpeg"
        )
        adapted = {
            **CV_DATA,
            "identity": {"first_name": "Awa", "last_name": "Diallo", "headline": "Cheffe de rayon"},
        }

        generated = await env.export().execute(
            env.user,
            template_id=template.id,
            formats=["pdf"],
            ats_safe=False,
            offer_id=None,
            dossier_id=uuid4(),
            cv=adapted,
        )

        [call] = env.engine.called("render_pdf")
        assert call["view"]["header"]["headline"] == "Cheffe de rayon"
        assert call["photo"] == b"\xff\xd8photo"
        assert generated.dossier_id is not None
        assert generated.full_name == "Awa Diallo"

    async def test_sans_cv_de_base_le_cv_recu_suffit(self, env: Env) -> None:
        template, _ = env.templates.publish()

        generated = await env.export().execute(
            env.user,
            template_id=template.id,
            formats=["pdf"],
            ats_safe=False,
            offer_id=None,
            cv=CV_DATA,
        )

        assert generated.pdf_key is not None
        assert env.engine.called("render_pdf")[0]["photo"] is None

    async def test_sans_cv_recu_le_cv_de_base_reste_exige(self, env: Env) -> None:
        template, _ = env.templates.publish()
        with pytest.raises(CvMissingError):
            await env.export().execute(
                env.user, template_id=template.id, formats=None, ats_safe=False, offer_id=None
            )


class TestCouleurPrincipaleDeLExport:
    """ADR-0034 : l'export garde la couleur avec laquelle il a été rendu."""

    async def test_l_export_rend_et_retient_la_couleur_choisie(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)

        generated = await env.export().execute(
            env.user,
            template_id=template.id,
            formats=["pdf"],
            ats_safe=False,
            offer_id=None,
            primary_color="#7B1E2B",
        )

        assert generated.primary_color == "#7B1E2B"
        assert env.generated.items[generated.id].primary_color == "#7B1E2B"
        [call] = env.engine.called("render_pdf")
        assert call["spec"]["page_decorations"][0]["stroke"] == "#7B1E2B", "le décor suit"
        assert call["spec"]["styles"]["section_title"]["color"] == "#1A5276", "le texte reste"

    async def test_sans_modele_l_export_prend_aussi_la_couleur_du_choix(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        await env.owned.save_selection(
            Selection(
                user_id=env.user,
                template_id=template.id,
                ats_safe=False,
                updated_at=NOW,
                primary_color="#2E7D32",
            )
        )

        generated = await env.export().execute(
            env.user, template_id=None, formats=["pdf"], ats_safe=None, offer_id=None
        )

        assert generated.primary_color == "#2E7D32"
        assert env.engine.called("render_pdf")[0]["spec"]["page_decorations"][0]["stroke"] == (
            "#2E7D32"
        )


class TestUneSeulePage:
    """Un CV qui déborde est resserré par paliers ; sinon il garde ses pages."""

    async def test_un_cv_qui_deborde_est_resserre_jusqu_a_tenir(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(
            env.user,
            data={
                **CV_DATA,
                "experiences": [
                    {
                        "position": "Lead",
                        "company": "A",
                        "missions": ["m1", "m2", "m3", "m4", "m5"],
                    }
                ],
            },
        )
        # Première passe : deux pages ; palier 1 : une page.
        env.engine.fits = [Fit(pages=2), Fit(pages=1, spacing=0.8)]

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=False, offer_id=None
        )

        assert generated.fit.pages == 1
        assert generated.fit.condensed == {"missions": 2}
        rendus = env.engine.called("render_pdf")
        assert len(rendus) == 2
        # La vue resserrée est celle qui a été rendue, lue par l'ATS et aperçue.
        derniere = rendus[-1]["view"]
        experiences = next(s for s in derniere["sections"] if s["kind"] == "experiences")
        assert len(experiences["items"][0]["bullet"]) == 3

    async def test_un_cv_que_rien_ne_fait_tenir_garde_ses_pages_entieres(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(
            env.user,
            data={
                **CV_DATA,
                "experiences": [
                    {"position": "Lead", "company": "A", "missions": ["m1", "m2", "m3", "m4"]}
                ],
            },
        )
        env.engine.fits = [Fit(pages=3), Fit(pages=2), Fit(pages=2)]

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=False, offer_id=None
        )

        # Trois rendus (entier, palier 1, palier 2) ; le CV entier est gardé, sur ses pages.
        assert len(env.engine.called("render_pdf")) == 3
        assert generated.fit.pages == 3
        assert generated.fit.condensed == {}

    async def test_un_cv_qui_tient_n_est_pas_touche(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=False, offer_id=None
        )

        assert len(env.engine.called("render_pdf")) == 1
        assert generated.fit.condensed == {}


class TestApercusDeLExport:
    async def test_chaque_page_est_rendue_en_image_et_rangee_avec_l_export(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        env.engine.pages = 2

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=False, offer_id=None
        )

        prefix = f"generated-cvs/{env.user}/{generated.id}"
        assert generated.preview_keys == (f"{prefix}/apercu-1.png", f"{prefix}/apercu-2.png")
        assert (Bucket.DOCUMENTS, f"{prefix}/apercu-2.png") in env.storage.objects
