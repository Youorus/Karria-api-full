"""Dériver une création Karria d'un modèle publié (`DeriveTemplateVersion`)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from documents.application.conftest import Env
from documents.fakes import PDF_BYTES, good_fidelity, make_spec
from karria.modules.documents.application import DeriveTemplateVersion
from karria.modules.documents.domain import VersionStatus
from karria.modules.documents.domain.errors import TemplateIngestionError, TemplateNotFoundError
from karria.shared.storage import Bucket


def derive(env: Env) -> DeriveTemplateVersion:
    return DeriveTemplateVersion(env.templates, env.engine, env.storage, env.clock)


async def published(env: Env, **kwargs: object) -> tuple[object, object]:
    template, version = env.templates.publish(spec=make_spec(columns=2, photo=True), **kwargs)  # type: ignore[arg-type]
    await env.storage.put(
        Bucket.TEMPLATES, version.source_key, PDF_BYTES, content_type="application/pdf"
    )
    return template, version


class TestDerivation:
    async def test_publie_une_nouvelle_version_distincte_du_meme_modele(self, env: Env) -> None:
        template, source = await published(env)
        env.engine.fidelity_pdf = replace(good_fidelity(), ssim=0.42, ssim_tolerant=0.45)

        done, report = await derive(env).execute("meridian", seed=2)

        assert done.template_id == template.id  # type: ignore[attr-defined]
        assert done.version == 2
        assert done.status is VersionStatus.PUBLISHED
        assert done.spec is not None
        assert done.spec["meta"]["origin"] == "karria"
        assert done.spec["source"]["derivation"]["seed"] == 2
        assert report["distance"]["ssim_tolerant"] == 0.45
        # Les polices de la dérivée ont été rangées, aucune n'est celle de la source.
        assert env.engine.called("ensure_fonts")
        source_fonts = {s["family"] for s in source.spec["styles"].values()}  # type: ignore[attr-defined]
        assert not source_fonts & {f["family"] for f in done.spec["fonts"]}
        # La fiche pointe la nouvelle version, étiquetée création Karria.
        updated = env.templates.templates[template.id]  # type: ignore[attr-defined]
        assert updated.published_version_id == done.id
        assert updated.published_version == 2
        assert "création Karria" in updated.tags
        # Les aperçus et le rendu-source de la version sont rangés.
        assert (Bucket.TEMPLATES, done.source_key) in env.storage.objects
        assert done.thumbnail_key is not None
        assert (Bucket.TEMPLATES, done.thumbnail_key) in env.storage.objects

    async def test_refuse_une_derivation_trop_proche_de_sa_source(self, env: Env) -> None:
        await published(env)
        # La mesure par défaut dit « quasi identique » : la porte inversée refuse.
        with pytest.raises(TemplateIngestionError) as refus:
            await derive(env).execute("meridian")
        assert "trop proche" in str(refus.value.details.get("detail"))
        assert env.engine.called("previews") == []

    async def test_l_essai_a_blanc_n_ecrit_rien(self, env: Env) -> None:
        template, _ = await published(env)
        env.engine.fidelity_pdf = replace(good_fidelity(), ssim_tolerant=0.3)
        before = dict(env.storage.objects)

        version, report = await derive(env).execute("meridian", dry_run=True)

        assert version.version == 2
        assert report["derivation"]["mirrored"] is True
        assert env.storage.objects == before
        assert env.templates.templates[template.id].published_version == 1  # type: ignore[attr-defined]

    async def test_derive_toujours_de_la_transcription_pas_d_une_derivation(self, env: Env) -> None:
        template, _ = await published(env)
        env.engine.fidelity_pdf = replace(good_fidelity(), ssim_tolerant=0.3)
        first, _ = await derive(env).execute("meridian", seed=1)
        assert env.templates.templates[template.id].published_version == 2  # type: ignore[attr-defined]

        second, report = await derive(env).execute("meridian", seed=2)

        # La v3 part de la v1 transcrite (miroir simple), pas de la v2 dérivée.
        assert report["derived_from"]["version"] == 1
        assert second.spec is not None
        assert second.spec["source"]["derivation"]["seed"] == 2
        assert first.spec is not None
        assert first.spec["source"]["derivation"]["seed"] == 1

    async def test_un_modele_inconnu_ou_non_publie_est_introuvable(self, env: Env) -> None:
        with pytest.raises(TemplateNotFoundError):
            await derive(env).execute("absent")
