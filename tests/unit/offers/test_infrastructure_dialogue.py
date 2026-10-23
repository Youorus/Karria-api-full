"""Le dialogue de recherche par le port LLM : la requête, la lecture de la sortie, les pannes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from doubles import FakeLanguageModel, refused_result, text_result
from karria.modules.offers.domain import (
    Intent,
    ModelOutputInvalidError,
    ModelUnavailableError,
    SearchCriteria,
)
from karria.modules.offers.infrastructure.dialogue import (
    OFF_TOPIC_REPLY,
    LlmDialogue,
    dialogue_schema,
)
from karria.modules.offers.infrastructure.prompts import SYSTEM
from karria.shared.llm import LlmCapabilityError, LlmUnavailableError

pytestmark = pytest.mark.unit

CRITERIA = {"query": "Serveur à Paris", "job_titles": ["serveur", "serveuse"]}


async def propose(llm: FakeLanguageModel, **kwargs: Any) -> Any:
    return await LlmDialogue(llm).propose(
        history=kwargs.get("history", []), current=kwargs.get("current"), message="serveur Paris"
    )


class TestRequete:
    async def test_la_consigne_l_historique_et_la_donnee_voyagent_separement(self) -> None:
        llm = FakeLanguageModel(text_result(json.dumps({"reply": "Compris."})))

        await propose(
            llm,
            history=[("user", "bonjour"), ("assistant", "Bonjour !")],
            current=SearchCriteria(**CRITERIA),
        )

        [request] = llm.requests
        assert request.task == "search_dialogue"
        assert request.system == SYSTEM
        assert request.history == (("user", "bonjour"), ("assistant", "Bonjour !"))
        text = str(request.parts[0])
        assert "<message>\nserveur Paris\n</message>" in text
        assert '"job_titles"' in text
        assert request.response_schema == dialogue_schema()
        assert request.thinking is False

    def test_le_schema_porte_les_criteres_et_les_exclusions(self) -> None:
        schema = dialogue_schema()
        assert set(schema["properties"]) == {
            "intent",
            "reply",
            "off_topic",
            "criteria",
            "suggestions",
        }
        assert schema["properties"]["intent"]["enum"] == [i.value for i in Intent]
        assert "excluded_titles" in json.dumps(schema)
        assert "$defs" not in json.dumps(schema)


class TestLecture:
    async def test_une_sortie_conforme_devient_une_proposition(self) -> None:
        out = {"reply": "C'est noté.", "criteria": CRITERIA, "suggestions": ["En CDI"]}
        proposal = await propose(FakeLanguageModel(text_result(json.dumps(out))))

        assert proposal.reply == "C'est noté."
        assert proposal.criteria is not None
        assert proposal.criteria.job_titles == ["serveur", "serveuse"]
        assert proposal.suggestions == ("En CDI",)
        assert proposal.off_topic is False

    async def test_un_refus_ou_un_vide_recentre_vers_la_recherche(self) -> None:
        for result in (refused_result("SAFETY"), text_result(None)):  # type: ignore[arg-type]
            proposal = await propose(FakeLanguageModel(result))
            assert proposal.off_topic is True
            assert proposal.criteria is None
            assert proposal.reply == OFF_TOPIC_REPLY

    async def test_une_sortie_hors_schema_ou_non_json_est_une_panne(self) -> None:
        for text in (json.dumps({"criteria": "pas un objet"}), "pas du json"):
            with pytest.raises(ModelOutputInvalidError):
                await propose(FakeLanguageModel(text_result(text)))

    async def test_les_pannes_du_port_deviennent_celles_du_module(self) -> None:
        for error in (LlmUnavailableError("HTTP 503", status=503), LlmCapabilityError("non")):
            with pytest.raises(ModelUnavailableError):
                await propose(FakeLanguageModel(error))
