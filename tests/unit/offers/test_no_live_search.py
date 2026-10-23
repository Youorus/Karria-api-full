"""La règle : une conversation ne déclenche jamais une recherche en direct sur une source externe.

La recherche et le dialogue ne lisent que **notre** index, tenu à jour par
la collecte continue (ADR-0016 bis). Deux garde-fous :

- import-linter interdit à `karria.modules` d'importer `karria.collector`
  et `jobspy` (contrat « Collecteur — l'API ne l'importe jamais ») ;
- ce test vérifie que le module `offers` ne connaît ni France Travail, ni
  un site d'offres, et que le chemin de la recherche ne touche que des ports
  internes : une recherche maigre ne fait que rendre une cible due.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import karria.modules.offers as offers_module
from karria.modules.offers.application import ApplyCriteria, SearchOffers

pytestmark = pytest.mark.unit

OFFERS_ROOT = Path(offers_module.__file__).parent
#: Ce qu'une source externe laisserait dans le code : un import, une URL d'hôte.
#: (La prose des docstrings peut nommer JobSpy ; le code, jamais.)
EXTERNAL_SOURCES = re.compile(
    r"^\s*(from|import)\s+jobspy"
    r"|https?://[^\s\"']*(francetravail|pole-emploi|indeed|hellowork|linkedin|glassdoor)",
    re.IGNORECASE | re.MULTILINE,
)


def test_le_module_offers_ne_connait_aucune_source_externe() -> None:
    offenders = [
        str(path.relative_to(OFFERS_ROOT))
        for path in OFFERS_ROOT.rglob("*.py")
        if EXTERNAL_SOURCES.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_la_recherche_ne_depend_que_de_ports_internes() -> None:
    """Les dépendances de la recherche sont nommées : index, plongement, base, cibles — jamais un site."""
    search_ports = set(SearchOffers.__init__.__code__.co_varnames[1:]) - {"self"}
    apply_ports = set(ApplyCriteria.__init__.__code__.co_varnames[1:]) - {"self"}

    assert search_ports == {"embedder", "index", "offers"}
    assert "targets" in apply_ports  # une recherche maigre rend une cible due…
    assert (
        not {"collector", "scraper", "france_travail", "sources"} & apply_ports
    )  # …et rien de plus
