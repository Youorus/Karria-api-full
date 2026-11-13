"""Le portrait d'exemple généré : à l'ingestion, à la dérivation, et sur un modèle déjà publié."""

from __future__ import annotations

from dataclasses import replace

import pytest

from documents.application.conftest import Env
from documents.application.test_ingestion import processing
from documents.fakes import (
    PDF_BYTES,
    PNG_BYTES,
    PORTRAIT_BYTES,
    FakePortraits,
    good_fidelity,
    make_analysis,
    make_spec,
)
from karria.modules.documents.application import (
    DeriveTemplateVersion,
    IngestTemplateVersion,
    RefreshSamplePortrait,
)
from karria.modules.documents.domain import VersionStatus
from karria.modules.documents.domain.errors import TemplateNotFoundError
from karria.shared.storage import Bucket


class TestALIngestion:
    async def test_la_photo_du_fournisseur_est_remplacee_par_le_portrait_genere(
        self, env: Env
    ) -> None:
        portraits = FakePortraits()
        env.engine.analysis = make_analysis(with_photo=True)
        version = await processing(env)

        done = await IngestTemplateVersion(
            env.templates, env.engine, env.storage, env.clock, portraits=portraits
        ).execute(version.id)

        assert done is not None
        assert done.status is VersionStatus.PUBLISHED
        assert [p.full_name for p in portraits.personas] == ["Jean Dupont"]
        key = done.assets["sample_photo.png"]
        assert env.storage.objects[(Bucket.TEMPLATES, key)].payload == PORTRAIT_BYTES
        # Le rendu de l'exemple a reçu le portrait, pas la photo du PDF.
        assert env.engine.called("render_pdf")[0]["photo"] == PORTRAIT_BYTES

    async def test_sans_portrait_genere_la_photo_du_pdf_reste(self, env: Env) -> None:
        env.engine.analysis = make_analysis(with_photo=True)
        version = await processing(env)

        done = await IngestTemplateVersion(
            env.templates, env.engine, env.storage, env.clock, portraits=FakePortraits(image=None)
        ).execute(version.id)

        assert done is not None
        key = done.assets["sample_photo.png"]
        assert env.storage.objects[(Bucket.TEMPLATES, key)].payload == PNG_BYTES

    async def test_un_modele_sans_photo_ne_demande_rien(self, env: Env) -> None:
        portraits = FakePortraits()
        env.engine.analysis = make_analysis(with_photo=False)
        version = await processing(env)
        await IngestTemplateVersion(
            env.templates, env.engine, env.storage, env.clock, portraits=portraits
        ).execute(version.id)
        assert portraits.personas == []


class TestALaDerivation:
    async def test_la_version_derivee_a_son_propre_portrait(self, env: Env) -> None:
        _template, source = env.templates.publish(
            spec=make_spec(columns=2, photo=True), assets={"sample_photo.png": "k/photo.png"}
        )
        await env.storage.put(
            Bucket.TEMPLATES, source.source_key, PDF_BYTES, content_type="application/pdf"
        )
        await env.storage.put(Bucket.TEMPLATES, "k/photo.png", PNG_BYTES, content_type="image/png")
        env.engine.fidelity_pdf = replace(good_fidelity(), ssim_tolerant=0.4)
        portraits = FakePortraits()

        done, _ = await DeriveTemplateVersion(
            env.templates, env.engine, env.storage, env.clock, portraits=portraits
        ).execute("meridian", seed=1)

        assert len(portraits.personas) == 1
        assert portraits.personas[0].seed != 0
        key = done.assets["sample_photo.png"]
        assert env.storage.objects[(Bucket.TEMPLATES, key)].payload == PORTRAIT_BYTES
        assert env.engine.called("render_pdf")[-1]["photo"] == PORTRAIT_BYTES


class TestSurUnModelePublie:
    async def test_refait_le_portrait_et_les_apercus(self, env: Env) -> None:
        _template, version = env.templates.publish(
            spec=make_spec(photo=True), assets={"sample_photo.png": "k/photo.png"}
        )
        await env.storage.put(Bucket.TEMPLATES, "k/photo.png", PNG_BYTES, content_type="image/png")

        done = await RefreshSamplePortrait(
            env.templates, env.engine, env.storage, FakePortraits(), env.clock
        ).execute("meridian")

        assert done is not None
        key = done.assets["sample_photo.png"]
        assert key.endswith("/assets/sample_photo.png")
        assert env.storage.objects[(Bucket.TEMPLATES, key)].payload == PORTRAIT_BYTES
        assert done.thumbnail_key is not None
        assert (Bucket.TEMPLATES, done.thumbnail_key) in env.storage.objects
        assert "portrait" in done.report
        assert env.templates.versions[version.id].assets["sample_photo.png"] == key

    async def test_un_modele_sans_photo_rend_none(self, env: Env) -> None:
        env.templates.publish(spec=make_spec(photo=False))
        done = await RefreshSamplePortrait(
            env.templates, env.engine, env.storage, FakePortraits(), env.clock
        ).execute("meridian")
        assert done is None

    async def test_un_modele_inconnu_est_introuvable(self, env: Env) -> None:
        with pytest.raises(TemplateNotFoundError):
            await RefreshSamplePortrait(
                env.templates, env.engine, env.storage, FakePortraits(), env.clock
            ).execute("absent")
