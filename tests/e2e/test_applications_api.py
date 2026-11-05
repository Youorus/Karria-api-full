"""Parcours HTTP complet d'un dossier de candidature : ouvrir, décider, retoucher, lettre, candidater."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from doubles import ApplicationsEnv, FakeCvAdapter, FakeLetterWriter, sample_letter_json
from karria.modules.applications.api import dependencies as deps
from karria.modules.applications.application import use_cases
from karria.modules.applications.domain import ModelUnavailableError
from karria.shared.http import SlidingWindowLimiter

pytestmark = pytest.mark.e2e

API = "/api/v1/applications"


async def open_dossier(client: AsyncClient, env: ApplicationsEnv, **body: Any) -> dict[str, Any]:
    response = await client.post(API, json={"offer_id": str(env.offer.offer_id), **body})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


class TestOuverture:
    async def test_sans_jeton_401(self, client: AsyncClient) -> None:
        assert (await client.get(API)).status_code == 401
        assert (await client.post(API, json={"offer_id": str(uuid4())})).status_code == 401

    async def test_ouvre_un_dossier_adapte(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()

        dossier = await open_dossier(client, applications_env)

        assert dossier["status"] == "brouillon"
        assert dossier["adaptation"] == "faite"
        assert dossier["offer"]["title"] == applications_env.offer.title
        assert dossier["offer"]["logo_url"] == "https://logos.test/abc123/128.webp"
        assert dossier["offer"]["keywords"]
        assert dossier["cv"]["identity"]["headline"] == "Conseillère de vente"
        assert "image_url" not in dossier["cv"]
        assert dossier["base_changed"] is False
        states = {p["path"]: p["state"] for p in dossier["propositions"]}
        assert states["identity.headline"] == "acceptee"
        assert states["skills"] == "proposee"
        assert dossier["coverage"]["ratio"] > 0
        assert dossier["letter"] is None
        assert dossier["applied_at"] is None

    async def test_redemander_rend_l_existant_en_200(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        first = await open_dossier(client, applications_env)

        again = await client.post(API, json={"offer_id": str(applications_env.offer.offer_id)})

        assert again.status_code == 200
        assert again.json()["id"] == first["id"]

    @pytest.mark.parametrize(
        ("prepare", "status", "reason"),
        [
            (lambda env: setattr(env.base_cvs, "data", None), 409, "cv_missing"),
            (lambda env: env.offers.cards.clear(), 404, "offer_not_found"),
        ],
    )
    async def test_les_refus_explicites(  # noqa: PLR0917 - fixtures et paramètres
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
        prepare: Any,
        status: int,
        reason: str,
    ) -> None:
        authenticate()
        prepare(applications_env)

        response = await client.post(API, json={"offer_id": str(applications_env.offer.offer_id)})

        assert response.status_code == status
        assert response.json()["error"]["details"]["reason"] == reason

    async def test_un_modele_premium_non_possede(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        locked = uuid4()
        applications_env.templates.locked.add(locked)

        response = await client.post(
            API, json={"offer_id": str(applications_env.offer.offer_id), "template_id": str(locked)}
        )

        assert response.status_code == 403
        assert response.json()["error"]["details"]["reason"] == "template_locked"

    async def test_un_modele_en_panne_rend_un_dossier_echoue_puis_se_relance(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        applications_env.adapter = FakeCvAdapter(error=ModelUnavailableError("panne"))
        dossier = await open_dossier(client, applications_env)
        assert dossier["adaptation"] == "echouee"
        assert dossier["adaptation_error"] == "ai_unavailable"

        applications_env.adapter = FakeCvAdapter()
        retried = await client.post(f"{API}/{dossier['id']}/adapt")
        assert retried.status_code == 200
        assert retried.json()["adaptation"] == "faite"

        again = await client.post(f"{API}/{dossier['id']}/adapt")
        assert again.status_code == 409
        assert again.json()["error"]["details"]["reason"] == "adaptation_not_failed"


class TestLectureEtListe:
    async def test_liste_detail_et_suppression(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)

        listed = await client.get(API)
        assert listed.status_code == 200
        cards = listed.json()["items"]
        assert [c["id"] for c in cards] == [dossier["id"]]
        assert cards[0]["pending_propositions"] == 3
        assert cards[0]["has_letter"] is False
        assert "cv" not in cards[0]

        assert (await client.get(f"{API}?applied=true")).json()["items"] == []

        detail = await client.get(f"{API}/{dossier['id']}")
        assert detail.status_code == 200
        assert detail.json()["cv"] == dossier["cv"]

        assert (await client.delete(f"{API}/{dossier['id']}")).status_code == 204
        missing = await client.get(f"{API}/{dossier['id']}")
        assert missing.status_code == 404
        assert missing.json()["error"]["details"]["reason"] == "dossier_not_found"

    async def test_le_dossier_d_une_autre_personne_est_introuvable(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)
        authenticate(uuid4())

        assert (await client.get(f"{API}/{dossier['id']}")).status_code == 404
        assert (await client.get(API)).json()["items"] == []


class TestModele:
    async def test_changer_de_modele_a_tout_moment_sans_rien_perdre(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)
        assert dossier["template_id"] is None
        first, second = uuid4(), uuid4()
        applications_env.clock.advance(timedelta(minutes=1))

        changed = await client.put(
            f"{API}/{dossier['id']}/template", json={"template_id": str(first)}
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert body["template_id"] == str(first)
        assert body["cv"] == dossier["cv"]
        assert body["propositions"] == dossier["propositions"]
        assert body["coverage"] == dossier["coverage"]
        assert body["updated_at"] > dossier["updated_at"]

        again = await client.put(
            f"{API}/{dossier['id']}/template", json={"template_id": str(second)}
        )
        assert again.json()["template_id"] == str(second)
        assert again.json()["cv"] == dossier["cv"]

        detail = await client.get(f"{API}/{dossier['id']}")
        assert detail.json()["template_id"] == str(second)
        cards = (await client.get(API)).json()["items"]
        assert cards[0]["template_id"] == str(second)

        reset = await client.put(f"{API}/{dossier['id']}/template", json={"template_id": None})
        assert reset.status_code == 200
        assert reset.json()["template_id"] is None

    async def test_un_modele_premium_non_possede_est_refuse(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)
        locked = uuid4()
        applications_env.templates.locked.add(locked)

        refused = await client.put(
            f"{API}/{dossier['id']}/template", json={"template_id": str(locked)}
        )

        assert refused.status_code == 403
        error = refused.json()["error"]
        assert error["details"]["reason"] == "template_locked"
        assert error["details"]["template_id"] == str(locked)
        assert (await client.get(f"{API}/{dossier['id']}")).json()["template_id"] is None

    async def test_dossier_inconnu_corps_incomplet_et_autre_personne(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)

        missing = await client.put(f"{API}/{uuid4()}/template", json={"template_id": None})
        assert missing.status_code == 404
        assert missing.json()["error"]["details"]["reason"] == "dossier_not_found"

        assert (await client.put(f"{API}/{dossier['id']}/template", json={})).status_code == 422

        authenticate(uuid4())
        other = await client.put(
            f"{API}/{dossier['id']}/template", json={"template_id": str(uuid4())}
        )
        assert other.status_code == 404


class TestAtelier:
    async def test_decider_puis_retoucher(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)
        skills = next(p for p in dossier["propositions"] if p["path"] == "skills")

        accepted = await client.post(
            f"{API}/{dossier['id']}/propositions/{skills['id']}", json={"decision": "accepter"}
        )
        assert accepted.status_code == 200
        assert accepted.json()["cv"]["skills"][0]["name"] == "Relation client"

        unknown = await client.post(
            f"{API}/{dossier['id']}/propositions/{uuid4()}", json={"decision": "refuser"}
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["details"]["reason"] == "proposition_not_found"

        bad = await client.post(
            f"{API}/{dossier['id']}/propositions/{skills['id']}", json={"decision": "peut-être"}
        )
        assert bad.status_code == 422

        cv = {**accepted.json()["cv"], "summary": "Mon texte à moi"}
        updated = await client.put(f"{API}/{dossier['id']}/cv", json={"cv": cv})
        assert updated.status_code == 200
        assert updated.json()["cv"]["summary"] == "Mon texte à moi"

        applications_env.validator.invalid = True
        invalid = await client.put(f"{API}/{dossier['id']}/cv", json={"cv": cv})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["details"]["reason"] == "cv_invalid"

    async def test_retoucher_pendant_l_adaptation(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        authenticate()
        applications_env.adapter = FakeCvAdapter(delay=30)
        monkeypatch.setattr(use_cases, "ADAPTATION_INLINE_SECONDS", 0.01)

        dossier = await open_dossier(client, applications_env)

        assert dossier["adaptation"] == "en_cours"
        assert applications_env.jobs.deferred == [UUID(dossier["id"])]
        blocked = await client.put(f"{API}/{dossier['id']}/cv", json={"cv": dossier["cv"]})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["details"]["reason"] == "adaptation_in_progress"


class TestLettre:
    async def test_ebauche_edition_reecriture(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)

        drafted = await client.post(f"{API}/{dossier['id']}/letter")
        assert drafted.status_code == 200
        letter = drafted.json()["letter"]
        assert [p["kind"] for p in letter["paragraphs"]] == [
            "interet_entreprise",
            "interet_poste",
            "competences",
            "pourquoi_moi",
        ]

        edited = await client.put(
            f"{API}/{dossier['id']}/letter",
            json={**sample_letter_json(), "closing": "Cordialement,"},
        )
        assert edited.status_code == 200
        assert edited.json()["letter"]["closing"] == "Cordialement,"

        invalid = await client.put(
            f"{API}/{dossier['id']}/letter", json={**sample_letter_json(), "paragraphs": []}
        )
        assert invalid.status_code == 422

        regenerated = await client.post(
            f"{API}/{dossier['id']}/letter/paragraphs/1/regenerate",
            json={"instruction": "plus court"},
        )
        assert regenerated.status_code == 200
        assert regenerated.json()["letter"]["paragraphs"][1]["text"] == "Paragraphe réécrit."

        too_far = await client.post(
            f"{API}/{dossier['id']}/letter/paragraphs/9/regenerate", json={}
        )
        assert too_far.status_code == 404
        assert too_far.json()["error"]["details"]["reason"] == "paragraph_not_found"

    async def test_sans_lettre_et_modele_en_panne(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)

        missing = await client.post(
            f"{API}/{dossier['id']}/letter/paragraphs/0/regenerate", json={}
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["details"]["reason"] == "letter_missing"

        applications_env.writer = FakeLetterWriter(error=ModelUnavailableError("panne"))
        down = await client.post(f"{API}/{dossier['id']}/letter")
        assert down.status_code == 503
        assert down.json()["error"]["details"]["reason"] == "ai_unavailable"


class TestCandidature:
    async def test_j_ai_candidate_puis_non(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)

        applied = await client.post(f"{API}/{dossier['id']}/applied")
        assert applied.status_code == 200
        assert applied.json()["status"] == "candidate"
        assert applied.json()["applied_at"] is not None
        assert [c["id"] for c in (await client.get(f"{API}?applied=true")).json()["items"]] == [
            dossier["id"]
        ]

        back = await client.delete(f"{API}/{dossier['id']}/applied")
        assert back.status_code == 200
        assert back.json()["status"] == "brouillon"
        assert back.json()["applied_at"] is None


class TestGardeFous:
    async def test_les_appels_au_modele_sont_limites_par_compte(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            deps, "_ai_limiter", SlidingWindowLimiter(max_hits=2, window_seconds=60)
        )
        authenticate()
        dossier = await open_dossier(client, applications_env)
        assert (await client.post(f"{API}/{dossier['id']}/letter")).status_code == 200

        blocked = await client.post(f"{API}/{dossier['id']}/letter")

        assert blocked.status_code == 429
        assert blocked.json()["error"]["details"]["reason"] == "ai_rate_limited"
        assert blocked.json()["error"]["details"]["retry_after"] >= 1
        # Un autre compte n'est pas concerné, et les routes sans modèle non plus.
        assert (await client.get(f"{API}/{dossier['id']}")).status_code == 200
        authenticate(uuid4())
        assert (
            await client.post(API, json={"offer_id": str(applications_env.offer.offer_id)})
        ).status_code == 201

    async def test_un_corps_de_cv_trop_lourd_est_refuse(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)
        heavy = {**dossier["cv"], "summary": "x" * (256 * 1024)}

        response = await client.put(f"{API}/{dossier['id']}/cv", json={"cv": heavy})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "cv_too_large"

    async def test_une_proposition_invalide_ne_s_applique_pas(
        self,
        client: AsyncClient,
        authenticate: Any,
        ai_consented: None,
        applications_env: ApplicationsEnv,
    ) -> None:
        authenticate()
        dossier = await open_dossier(client, applications_env)
        skills = next(p for p in dossier["propositions"] if p["path"] == "skills")
        applications_env.validator.invalid = True

        response = await client.post(
            f"{API}/{dossier['id']}/propositions/{skills['id']}", json={"decision": "accepter"}
        )

        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "proposition_invalid"
