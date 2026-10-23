"""La mémoire de Karria sur une personne : bornée, fusionnée par la règle, rendue en données."""

from __future__ import annotations

import pytest

from karria.modules.offers.domain import (
    MEMORY_LIST_MAX,
    MEMORY_SUMMARY_MAX_LENGTH,
    KarriaMemory,
    MemoryFacts,
    merge_memory,
)

pytestmark = pytest.mark.unit


class TestBornes:
    def test_les_listes_sont_dedoublonnees_et_bornees(self) -> None:
        facts = MemoryFacts(
            target_jobs=[f"métier {i}" for i in range(20)] + ["Métier 0", "  métier   1 "]
        )
        assert len(facts.target_jobs) == MEMORY_LIST_MAX
        assert facts.target_jobs[0] == "métier 0"

    def test_un_fait_trop_long_est_coupe(self) -> None:
        facts = MemoryFacts(constraints=["x" * 500], summary="y" * 2_000)
        assert len(facts.constraints[0]) == 120
        assert len(facts.summary) == MEMORY_SUMMARY_MAX_LENGTH

    def test_le_vide_se_sait_vide(self) -> None:
        assert MemoryFacts().is_empty
        assert not MemoryFacts(situation=["en reconversion"]).is_empty
        assert KarriaMemory().facts.is_empty


class TestRendu:
    def test_le_rendu_est_court_et_etiquete(self) -> None:
        facts = MemoryFacts(
            target_jobs=["serveur", "barman"],
            locations=["Lyon, 10 km, à vélo"],
            constraints=["temps plein"],
            summary="Serveuse expérimentée qui veut quitter les coupures.",
        )
        rendered = facts.render()
        assert rendered.startswith("Serveuse expérimentée")
        assert "Métiers visés : serveur ; barman" in rendered
        assert "Lieux et mobilité : Lyon, 10 km, à vélo" in rendered
        assert "Préférences" not in rendered
        assert MemoryFacts().render() == ""


class TestFusion:
    def test_les_faits_nouveaux_rejoignent_les_connus_sans_doublon(self) -> None:
        current = MemoryFacts(target_jobs=["serveur"], constraints=["temps plein"], summary="A")
        proposed = MemoryFacts(target_jobs=["Serveur", "barman"], locations=["Lyon"])

        merged = merge_memory(current, proposed)

        assert merged.target_jobs == ["Serveur", "barman"]
        assert merged.constraints == ["temps plein"]
        assert merged.locations == ["Lyon"]
        assert merged.summary == "A"

    def test_le_resume_propose_remplace_l_ancien(self) -> None:
        merged = merge_memory(MemoryFacts(summary="ancien"), MemoryFacts(summary="nouveau"))
        assert merged.summary == "nouveau"

    def test_quand_la_liste_deborde_les_plus_recents_restent(self) -> None:
        current = MemoryFacts(preferences=[f"p{i}" for i in range(MEMORY_LIST_MAX)])
        merged = merge_memory(current, MemoryFacts(preferences=["nouvelle"]))
        assert merged.preferences[-1] == "nouvelle"
        assert "p0" not in merged.preferences
        assert len(merged.preferences) == MEMORY_LIST_MAX
