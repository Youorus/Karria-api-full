"""Suggérer à la saisie et matérialiser le vocabulaire, avec des doublures : aucune I/O."""

from __future__ import annotations

import pytest

from doubles import InMemorySuggestionIndex, StaticOfferVocabulary
from karria.modules.cv.application import RefreshSuggestions, SuggestCompletions
from karria.modules.cv.domain import SUGGESTION_LIMIT, SuggestionKind, VocabularyEntry

pytestmark = pytest.mark.unit


def entry(label: str, folded: str, weight: int) -> VocabularyEntry:
    return VocabularyEntry(label=label, folded=folded, weight=weight)


class TestSuggerer:
    async def test_une_famille_materialisee_interroge_l_index_avec_la_saisie_pliee(self) -> None:
        index = InMemorySuggestionIndex()
        index.entries[SuggestionKind.CITY] = [entry("Paris", "paris", 900), entry("Pau", "pau", 20)]

        found = await SuggestCompletions(index).execute(SuggestionKind.CITY, "  PÀ ")

        assert found == ["Paris", "Pau"]
        assert index.searches == [(SuggestionKind.CITY, "pa", SUGGESTION_LIMIT)]

    async def test_une_famille_embarquee_ne_touche_pas_l_index(self) -> None:
        index = InMemorySuggestionIndex()

        found = await SuggestCompletions(index).execute(SuggestionKind.LANGUAGE, "esp")

        assert found[0] == "Espagnol"
        assert index.searches == []

    async def test_une_saisie_vide_ne_rend_rien_et_ne_cherche_pas(self) -> None:
        index = InMemorySuggestionIndex()

        assert await SuggestCompletions(index).execute(SuggestionKind.TITLE, "  ") == []
        assert index.searches == []


class TestMaterialiser:
    async def test_chaque_famille_est_nettoyee_puis_remplacee(self) -> None:
        index = InMemorySuggestionIndex()
        index.entries[SuggestionKind.CITY] = [entry("Ancienne", "ancienne", 1)]
        vocabulary = StaticOfferVocabulary(
            {
                SuggestionKind.TITLE: [("Serveur H/F", 12), ("serveur", 3), ("Unique", 1)],
                SuggestionKind.COMPANY: [("CARREFOUR", 40), ("Lidl", 2)],
                SuggestionKind.CITY: [("Paris", 500), ("Lyon", 80)],
            }
        )

        report = await RefreshSuggestions(vocabulary, index).execute()

        assert report.written == {
            SuggestionKind.CITY: 2,
            SuggestionKind.COMPANY: 2,
            SuggestionKind.TITLE: 1,
        }
        assert [e.label for e in index.entries[SuggestionKind.TITLE]] == ["Serveur"]
        assert index.entries[SuggestionKind.TITLE][0].weight == 15
        assert [e.label for e in index.entries[SuggestionKind.COMPANY]] == ["Carrefour", "Lidl"]
        assert [e.label for e in index.entries[SuggestionKind.CITY]] == ["Paris", "Lyon"]

    async def test_un_vocabulaire_vide_ne_remplace_pas_ce_qu_on_servait(self) -> None:
        index = InMemorySuggestionIndex()
        index.entries[SuggestionKind.CITY] = [entry("Paris", "paris", 1)]

        report = await RefreshSuggestions(StaticOfferVocabulary({}), index).execute()

        assert report.written[SuggestionKind.CITY] == 0
        assert [e.label for e in index.entries[SuggestionKind.CITY]] == ["Paris"]
