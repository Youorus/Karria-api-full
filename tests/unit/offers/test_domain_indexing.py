"""Les textes plongés et lexicaux, et la version d'index."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from karria.modules.offers.domain import (
    Company,
    ContractType,
    ExperienceLevel,
    Location,
    Offer,
    RemoteMode,
    Salary,
    Schedule,
    Source,
    body_text,
    embedding_text,
    index_version,
    title_text,
    uuid7,
)
from karria.modules.offers.domain.indexing import (
    EMBEDDING_DESCRIPTION_CHARS,
    EMBEDDING_TEXT_VERSION,
    LEXICAL_DESCRIPTION_CHARS,
    cut,
    traits,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def make_offer(**changes: object) -> Offer:
    offer = Offer(
        id=uuid7(),
        source=Source.HELLOWORK,
        source_id="hw-1",
        title="Aide-soignant de nuit H/F",
        company=Company(name="EHPAD Les Tilleuls"),
        description="Vous accompagnez les résidents\ndans les gestes du quotidien. " * 40,
        location=Location(label="Marseille 8e", city="Marseille"),
        contract_type=ContractType.CDI,
        remote=RemoteMode.ON_SITE,
        schedule=Schedule.FULL_TIME,
        experience=ExperienceLevel.BEGINNER,
        salary=Salary(min_monthly=None, max_monthly=None, raw=None),
        published_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        apply_url=None,
        origin_url=None,
    )
    return dataclasses.replace(offer, **changes)  # type: ignore[arg-type]


class TestTexteDense:
    def test_len_tete_porte_titre_employeur_lieu_et_traits(self) -> None:
        lines = embedding_text(make_offer()).split("\n")
        assert lines[0] == "Aide-soignant de nuit H/F"
        assert lines[1] == "EHPAD Les Tilleuls — Marseille 8e"
        assert lines[2] == "CDI, sur site, temps plein, debutant"

    def test_la_description_est_bornee_et_sur_une_ligne(self) -> None:
        body = embedding_text(make_offer()).split("\n")[-1]
        assert len(body) <= EMBEDDING_DESCRIPTION_CHARS
        assert "\n" not in body

    def test_une_offre_nue_se_reduit_au_titre_et_a_la_description(self) -> None:
        bare = make_offer(
            company=None,
            location=None,
            contract_type=None,
            remote=None,
            schedule=None,
            experience=None,
            description="Courte.",
        )
        assert embedding_text(bare) == "Aide-soignant de nuit H/F\nCourte."
        assert traits(bare) == []


class TestTextesLexicaux:
    def test_le_titre_seul_pour_la_branche_dintitule(self) -> None:
        assert title_text(make_offer()) == "Aide-soignant de nuit H/F"

    def test_le_corps_porte_titre_employeur_et_description_bornee(self) -> None:
        text = body_text(make_offer())
        assert text.startswith("Aide-soignant de nuit H/F EHPAD Les Tilleuls Vous accompagnez")
        assert len(text) <= LEXICAL_DESCRIPTION_CHARS + 100

    def test_le_corps_sans_employeur(self) -> None:
        assert body_text(make_offer(company=None, description="Soins.")) == (
            "Aide-soignant de nuit H/F Soins."
        )


class TestVersion:
    def test_la_version_combine_modele_et_composition(self) -> None:
        assert index_version("e5@int8") == f"e5@int8@{EMBEDDING_TEXT_VERSION}"

    def test_la_coupe_respecte_les_mots(self) -> None:
        assert cut("service en salle", 11) == "service en"
        assert cut("court", 10) == "court"
