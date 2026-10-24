"""Historique, lecture, suppression, explication du score, oubli d'un compte."""

from __future__ import annotations

from uuid import uuid4

import pytest

from documents.application.conftest import Env
from documents.fakes import PDF_BYTES, FakeAtsExplainer, make_generated
from karria.modules.documents.application import (
    DeleteGeneratedCv,
    ExplainAts,
    ForgetUser,
    GetGeneratedCv,
    ListGeneratedCvs,
)
from karria.modules.documents.domain.errors import GeneratedCvNotFoundError
from karria.modules.documents.domain.templates import ExplanationStatus
from karria.shared.storage import Bucket

pytestmark = pytest.mark.unit

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def stored(env: Env, *keys: str) -> None:
    for key in keys:
        content_type = DOCX if key.endswith(".docx") else "application/pdf"
        await env.storage.put(Bucket.DOCUMENTS, key, PDF_BYTES, content_type=content_type)


class TestHistorique:
    async def test_l_historique_rend_les_exports_de_la_personne_pagines(self, env: Env) -> None:
        mine = [make_generated(env.user) for _ in range(3)]
        for g in [*mine, make_generated(uuid4())]:
            await env.generated.add(g)

        items, total = await ListGeneratedCvs(env.generated).execute(env.user, limit=2, offset=0)

        assert total == 3
        assert len(items) == 2
        assert all(g.user_id == env.user for g in items)

    async def test_un_export_de_la_personne_se_lit(self, env: Env) -> None:
        generated = make_generated(env.user)
        await env.generated.add(generated)

        assert await GetGeneratedCv(env.generated).execute(env.user, generated.id) == generated

    async def test_un_export_inconnu_n_existe_pas(self, env: Env) -> None:
        with pytest.raises(GeneratedCvNotFoundError):
            await GetGeneratedCv(env.generated).execute(env.user, uuid4())

    async def test_l_export_d_une_autre_personne_n_existe_pas(self, env: Env) -> None:
        generated = make_generated(uuid4())
        await env.generated.add(generated)

        with pytest.raises(GeneratedCvNotFoundError):
            await GetGeneratedCv(env.generated).execute(env.user, generated.id)


class TestSuppression:
    async def test_la_suppression_efface_l_export_et_ses_deux_fichiers(self, env: Env) -> None:
        generated = make_generated(env.user, pdf_key="g/cv.pdf", docx_key="g/cv.docx")
        await env.generated.add(generated)
        await stored(env, "g/cv.pdf", "g/cv.docx")

        await DeleteGeneratedCv(env.generated, env.storage).execute(env.user, generated.id)

        assert generated.id not in env.generated.items
        assert env.storage.objects == {}

    async def test_la_suppression_d_un_export_pdf_seul_ne_cherche_pas_de_docx(
        self, env: Env
    ) -> None:
        generated = make_generated(env.user, pdf_key=None, docx_key="g/cv.docx")
        await env.generated.add(generated)
        await stored(env, "g/cv.docx", "autre/cv.pdf")

        await DeleteGeneratedCv(env.generated, env.storage).execute(env.user, generated.id)

        assert list(env.storage.objects) == [(Bucket.DOCUMENTS, "autre/cv.pdf")]

    async def test_l_export_d_une_autre_personne_ne_se_supprime_pas(self, env: Env) -> None:
        generated = make_generated(uuid4())
        await env.generated.add(generated)

        with pytest.raises(GeneratedCvNotFoundError):
            await DeleteGeneratedCv(env.generated, env.storage).execute(env.user, generated.id)
        assert generated.id in env.generated.items


class TestExplication:
    async def test_une_explication_redigee_rend_l_export_pret(self, env: Env) -> None:
        generated = make_generated(env.user)
        await env.generated.add(generated)
        explainer = FakeAtsExplainer({"summary": "Bon CV."})

        status = await ExplainAts(env.generated, explainer).execute(generated.id)

        assert status is ExplanationStatus.READY
        assert explainer.reports == [generated.ats]
        assert env.generated.items[generated.id].explanation == {"summary": "Bon CV."}

    async def test_sans_explication_l_export_est_marque_indisponible(self, env: Env) -> None:
        generated = make_generated(env.user)
        await env.generated.add(generated)

        status = await ExplainAts(env.generated, FakeAtsExplainer(None)).execute(generated.id)

        assert status is ExplanationStatus.UNAVAILABLE
        assert env.generated.items[generated.id].explanation_status is (
            ExplanationStatus.UNAVAILABLE
        )

    async def test_sans_redacteur_l_export_est_marque_indisponible(self, env: Env) -> None:
        generated = make_generated(env.user)
        await env.generated.add(generated)

        assert await ExplainAts(env.generated, None).execute(generated.id) is (
            ExplanationStatus.UNAVAILABLE
        )

    async def test_un_export_inconnu_ou_deja_explique_est_ignore(self, env: Env) -> None:
        done = make_generated(env.user, status=ExplanationStatus.READY)
        await env.generated.add(done)
        explainer = FakeAtsExplainer({"summary": "x"})

        assert await ExplainAts(env.generated, explainer).execute(uuid4()) is None
        assert await ExplainAts(env.generated, explainer).execute(done.id) is None
        assert explainer.reports == []


class TestOubli:
    async def test_l_oubli_efface_exports_fichiers_possessions_et_choix(self, env: Env) -> None:
        a = make_generated(env.user, pdf_key="a/cv.pdf", docx_key="a/cv.docx")
        b = make_generated(env.user, pdf_key=None, docx_key=None)
        other = make_generated(uuid4(), pdf_key="o/cv.pdf")
        for g in (a, b, other):
            await env.generated.add(g)
        await stored(env, "a/cv.pdf", "a/cv.docx", "o/cv.pdf")
        env.owned.give(env.user, uuid4())

        forgotten = await ForgetUser(env.generated, env.owned, env.storage).execute(env.user)

        assert forgotten == 2
        assert list(env.storage.objects) == [(Bucket.DOCUMENTS, "o/cv.pdf")]
        assert list(env.generated.items) == [other.id]
        assert env.owned.forgotten == [env.user]
        assert env.owned.ownerships == []
