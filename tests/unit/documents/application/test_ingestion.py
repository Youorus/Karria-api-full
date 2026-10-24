"""Dépôt et ingestion d'un modèle : rien n'est publié sous le seuil de qualité."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from documents.application.conftest import Env
from documents.fakes import (
    NOW,
    PDF_BYTES,
    PNG_BYTES,
    bad_fidelity,
    make_analysis,
    make_spec,
    make_template,
    make_version,
)
from karria.modules.documents.application import IngestTemplateVersion, SubmitTemplate
from karria.modules.documents.application.use_cases import version_prefix
from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.templates import TemplateVersion, VersionStatus
from karria.shared.database import NullUnitOfWork
from karria.shared.storage import Bucket

pytestmark = pytest.mark.unit

SOURCE = b"%PDF-1.7 source"


def submit(env: Env) -> SubmitTemplate:
    return SubmitTemplate(env.templates, env.storage, env.jobs, env.clock, NullUnitOfWork())


def ingest(env: Env) -> IngestTemplateVersion:
    return IngestTemplateVersion(env.templates, env.engine, env.storage, env.clock)


def test_le_prefixe_d_une_version_range_ses_fichiers_par_modele_et_numero() -> None:
    template_id = uuid4()

    assert version_prefix(template_id, 3) == f"templates/{template_id}/v3"


class TestDepot:
    async def test_un_fichier_qui_n_est_pas_un_pdf_est_refuse(self, env: Env) -> None:
        with pytest.raises(TemplateIngestionError):
            await submit(env).execute(b"GIF89a", name="Meridian")
        assert env.templates.templates == {}

    async def test_un_nouveau_modele_est_cree_avec_une_version_en_analyse(self, env: Env) -> None:
        version = await submit(env).execute(
            SOURCE, name="  Meridian Pro ", is_premium=True, price=Decimal("4.99")
        )

        template = env.templates.templates[version.template_id]
        assert template.name == "meridian-pro"
        assert template.label == "Meridian Pro"
        assert template.is_premium is True
        assert template.price == Decimal("4.99")
        assert template.published_version_id is None
        assert version.status is VersionStatus.PROCESSING
        assert version.version == 1
        assert version.source_key == f"templates/{template.id}/v1/source.pdf"
        assert await env.storage.get(Bucket.TEMPLATES, version.source_key) == SOURCE
        assert env.jobs.ingestions == [version.id]

    async def test_un_modele_existant_recoit_une_nouvelle_version(self, env: Env) -> None:
        first = await submit(env).execute(SOURCE, name="Meridian", label="Méridien")

        second = await submit(env).execute(SOURCE, name="meridian", defer=False)

        assert second.template_id == first.template_id
        assert second.version == 2
        template = env.templates.templates[first.template_id]
        assert template.label == "Méridien"
        assert template.updated_at == NOW
        assert env.jobs.ingestions == [first.id]

    async def test_un_libelle_donne_remplace_celui_du_modele_existant(self, env: Env) -> None:
        first = await submit(env).execute(SOURCE, name="Meridian")

        await submit(env).execute(SOURCE, name="Meridian", label="Nouveau")

        assert env.templates.templates[first.template_id].label == "Nouveau"


async def processing(env: Env, **template_changes: object) -> TemplateVersion:
    template = replace(make_template(), **template_changes)  # type: ignore[arg-type]
    await env.templates.save(template)
    version = make_version(template.id, status=VersionStatus.PROCESSING, spec=None)
    await env.templates.save_version(version)
    await env.storage.put(
        Bucket.TEMPLATES, version.source_key, SOURCE, content_type="application/pdf"
    )
    return version


class TestIngestion:
    async def test_une_version_fidele_est_publiee_et_le_modele_la_pointe(self, env: Env) -> None:
        version = await processing(env)
        env.engine.analysis = make_analysis(with_photo=True)

        done = await ingest(env).execute(version.id)

        assert done is not None
        assert done.status is VersionStatus.PUBLISHED
        assert done.published_at == NOW
        assert done.error is None
        assert done.thumbnail_blurhash == "LKO2?U%2Tw=w"
        assert done.report["gate"] == {"passed": True, "failures": []}
        assert done.report["fidelity"]["docx"]["target"] == 0.80
        assert done.report["substitutions"] == [{"family": "Avenir", "origin": "substitute"}]
        assert set(done.report["timings"]) == {"analyze_s", "render_pdf_s", "measure_s", "docx_s"}
        prefix = version_prefix(version.template_id, 1)
        assert done.assets == {
            "icon_phone.png": f"{prefix}/assets/icon_phone.png",
            "sample_photo.png": f"{prefix}/assets/sample_photo.png",
        }
        for key in (
            done.thumbnail_key,
            done.preview_key,
            done.full_key,
            f"{prefix}/sample_render.pdf",
            f"{prefix}/sample_docx.pdf",
        ):
            assert key is not None
            assert await env.storage.exists(Bucket.TEMPLATES, key)
        assert env.templates.versions[version.id] == done
        render = env.engine.called("render_pdf")[0]
        assert render["photo"] == PNG_BYTES
        assert render["assets"] == {"icon_phone.png": PNG_BYTES}
        assert render["view"]["sample"] is True
        template = env.templates.templates[version.template_id]
        assert template.published_version_id == done.id
        assert template.published_version == 1
        assert template.has_photo is True
        assert template.primary_color == "#1A5276"
        assert template.font_family == "Inter"
        assert template.category == "classique"
        assert template.description == "Sobre."
        assert template.tags == ("sobre",)

    async def test_la_fiche_garde_ce_que_l_administration_a_deja_renseigne(self, env: Env) -> None:
        version = await processing(env, category="créatif", description="À moi.", tags=("mien",))
        env.engine.analysis = make_analysis(spec=make_spec(meta={}))

        await ingest(env).execute(version.id)

        template = env.templates.templates[version.template_id]
        assert (template.category, template.description, template.tags) == (
            "créatif",
            "À moi.",
            ("mien",),
        )

    async def test_une_version_sans_meta_ni_photo_se_publie(self, env: Env) -> None:
        version = await processing(env)
        spec = make_spec()
        del spec["meta"]
        env.engine.analysis = make_analysis(spec=spec)

        done = await ingest(env).execute(version.id)

        assert done is not None
        assert done.status is VersionStatus.PUBLISHED
        assert env.engine.called("render_pdf")[0]["photo"] is None
        assert env.templates.templates[version.template_id].category == ""

    async def test_une_version_sous_le_seuil_part_en_revue_sans_etre_publiee(
        self, env: Env
    ) -> None:
        version = await processing(env)
        env.engine.analysis = make_analysis()
        env.engine.fidelity_pdf = bad_fidelity()

        done = await ingest(env).execute(version.id)

        assert done is not None
        assert done.status is VersionStatus.REVIEW
        assert done.published_at is None
        assert done.report["gate"]["passed"] is False
        assert done.report["gate"]["failures"]
        template = env.templates.templates[version.template_id]
        assert template.published_version_id is None
        assert template.category == "classique"

    async def test_des_lignes_sans_role_envoient_la_version_en_revue(self, env: Env) -> None:
        version = await processing(env)
        env.engine.analysis = make_analysis(unlabeled=["l3", "l7"])

        done = await ingest(env).execute(version.id)

        assert done is not None
        assert done.status is VersionStatus.REVIEW
        assert done.report["gate"]["failures"] == ["lignes sans rôle : l3, l7"]

    async def test_un_pdf_illisible_fait_echouer_la_version_avec_la_raison(self, env: Env) -> None:
        version = await processing(env)
        env.engine.analyze_error = ValueError("aucune rubrique")

        done = await ingest(env).execute(version.id)

        assert done is not None
        assert done.status is VersionStatus.FAILED
        assert done.error == "aucune rubrique"
        assert env.templates.versions[version.id] == done

    async def test_une_erreur_d_ingestion_pendant_le_rendu_fait_echouer_la_version(
        self, env: Env
    ) -> None:
        version = await processing(env)
        env.engine.analysis = make_analysis()
        env.engine.render_error = TemplateIngestionError("page vide")

        done = await ingest(env).execute(version.id)

        assert done is not None
        assert done.status is VersionStatus.FAILED
        assert done.error == "page vide"

    async def test_une_panne_imprevue_fait_echouer_la_version_sans_lever(self, env: Env) -> None:
        version = await processing(env)
        env.engine.analysis = make_analysis()
        env.engine.render_error = RuntimeError("gotenberg a planté")

        done = await ingest(env).execute(version.id)

        assert done is not None
        assert done.status is VersionStatus.FAILED
        assert done.error == "RuntimeError: gotenberg a planté"

    async def test_une_version_deja_traitee_est_rendue_telle_quelle(self, env: Env) -> None:
        template = make_template()
        version = make_version(template.id, status=VersionStatus.PUBLISHED, spec=make_spec())
        await env.templates.save_version(version)

        assert await ingest(env).execute(version.id) == version
        assert env.engine.calls == []

    async def test_une_version_inconnue_ne_rend_rien(self, env: Env) -> None:
        assert await ingest(env).execute(uuid4()) is None

    async def test_une_version_dont_le_modele_a_disparu_ne_rend_rien(self, env: Env) -> None:
        version = make_version(uuid4(), status=VersionStatus.PROCESSING, spec=None)
        await env.templates.save_version(version)

        assert await ingest(env).execute(version.id) is None
        assert env.engine.calls == []

    async def test_le_pdf_source_est_celui_du_depot(self, env: Env) -> None:
        version = await processing(env)
        env.engine.analysis = make_analysis()

        await ingest(env).execute(version.id)

        assert env.engine.called("analyze") == [SOURCE]
        assert PDF_BYTES in env.engine.called("previews")
