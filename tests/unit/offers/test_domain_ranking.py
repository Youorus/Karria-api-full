"""Du candidat à l'offre classée : paliers, filtre dur, porte d'intitulé, score, raisons."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from karria.modules.offers.domain import (
    Company,
    ContractType,
    Location,
    LocationCriteria,
    MatchQuality,
    Offer,
    RemoteMode,
    Salary,
    SearchCriteria,
    Source,
    quality_of,
    rank,
    reasons_for,
    uuid7,
)
from karria.modules.offers.domain.ranking import (
    QUALITY_HIGH_SCORE,
    QUALITY_MEDIUM_SCORE,
    TITLE_PRIMARY_SCORE,
    TITLE_SYNONYM_SCORE,
    TITLE_WEIGHT,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, tzinfo=UTC)
PARIS = LocationCriteria(label="Paris", latitude=48.8566, longitude=2.3522)
VERSAILLES = Location(label="Versailles", city="Versailles", latitude=48.8049, longitude=2.1204)


def make_offer(**changes: object) -> Offer:
    offer = Offer(
        id=uuid7(),
        source=Source.INDEED,
        source_id="in-1",
        title="Serveur H/F",
        company=Company(name="Le Bistrot"),
        description="Service en salle\net en terrasse. " * 30,
        location=VERSAILLES,
        contract_type=ContractType.CDI,
        remote=None,
        schedule=None,
        experience=None,
        salary=Salary(min_monthly=1_900, max_monthly=None, raw=None),
        published_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        apply_url=None,
        origin_url=None,
    )
    return dataclasses.replace(offer, **changes)  # type: ignore[arg-type]


def criteria(**fields: object) -> SearchCriteria:
    base: dict[str, object] = {
        "query": "serveur à Paris",
        "job_titles": ["serveur"],
        "location": PARIS,
    }
    base.update(fields)
    return SearchCriteria.model_validate(base)


class TestPaliers:
    def test_les_trois_paliers(self) -> None:
        assert quality_of(QUALITY_HIGH_SCORE) is MatchQuality.HIGH
        assert quality_of(QUALITY_MEDIUM_SCORE) is MatchQuality.MEDIUM
        assert quality_of(QUALITY_MEDIUM_SCORE - 0.01) is MatchQuality.LOW


class TestClassement:
    def test_la_premiere_offre_du_rappel_qui_dit_le_metier_vaut_le_maximum(self) -> None:
        offer = make_offer()

        (ranked,) = rank([offer], criteria())

        assert ranked.score == TITLE_PRIMARY_SCORE
        assert ranked.quality is MatchQuality.HIGH

    def test_le_rang_de_rappel_departage_a_intitule_egal(self) -> None:
        a, b = make_offer(), make_offer()

        ranked = rank([a, b], criteria())

        assert [item.offer_id for item in ranked] == [a.id, b.id]
        assert ranked[1].score == round(TITLE_WEIGHT + (1 - TITLE_WEIGHT) * 0.5, 4)

    def test_un_synonyme_lointain_passe_apres_le_metier_dit(self) -> None:
        found = criteria(job_titles=["serveur", "serveuse", "chef de rang"])

        (synonym,) = rank([make_offer(title="Chef de rang H/F")], found)
        (primary,) = rank([make_offer()], found)

        assert synonym.score == round(TITLE_WEIGHT * TITLE_SYNONYM_SCORE + (1 - TITLE_WEIGHT), 4)
        assert synonym.score < primary.score

    def test_un_autre_metier_ne_passe_pas_la_porte(self) -> None:
        assert rank([make_offer(title="Chef de partie H/F")], criteria()) == []

    def test_le_filtre_dur_sapplique(self) -> None:
        assert rank([make_offer()], criteria(contract_types=["cdd"])) == []

    def test_le_nombre_est_limite(self) -> None:
        offers = [make_offer() for _ in range(3)]

        assert len(rank(offers, criteria(), limit=2)) == 2

    def test_un_rappel_vide_rend_un_fil_vide(self) -> None:
        assert rank([], criteria()) == []


class TestRaisons:
    def test_contrat_ville_et_distance(self) -> None:
        assert reasons_for(make_offer(), criteria(contract_types=["cdi"])) == (
            "CDI",
            "Versailles · 18 km",
        )

    def test_a_moins_dun_kilometre_la_ville_seule(self) -> None:
        here = Location(label="Paris", city="Paris", latitude=48.8566, longitude=2.3522)
        assert reasons_for(make_offer(location=here), criteria()) == ("Paris",)

    def test_sans_point_de_recherche_pas_de_distance(self) -> None:
        found = criteria(location=None, whole_france=True)
        assert reasons_for(make_offer(), found) == ()

    def test_un_lieu_sans_ville_ne_donne_pas_de_raison(self) -> None:
        nameless = Location(label="Île-de-France", latitude=48.85, longitude=2.35)
        assert reasons_for(make_offer(location=nameless), criteria()) == ()

    def test_un_lieu_sans_coordonnees_ne_donne_pas_de_distance(self) -> None:
        unplaced = Location(label="Versailles", city="Versailles")
        assert reasons_for(make_offer(location=unplaced), criteria()) == ()

    def test_le_teletravail_complet_se_dit(self) -> None:
        assert "Télétravail" in reasons_for(make_offer(remote=RemoteMode.REMOTE), criteria())

    def test_lhybride_ne_se_dit_que_si_demande(self) -> None:
        offer = make_offer(remote=RemoteMode.HYBRID)
        assert "Hybride" not in reasons_for(offer, criteria())
        assert "Hybride" in reasons_for(offer, criteria(remote=["hybride"]))

    def test_le_salaire_se_dit_si_demande_et_au_plus_trois_raisons(self) -> None:
        found = criteria(contract_types=["cdi"], salary_min_monthly=1_800)
        reasons = reasons_for(make_offer(remote=RemoteMode.REMOTE), found)
        assert reasons == ("CDI", "Versailles · 18 km", "Télétravail")
        assert reasons_for(make_offer(), criteria(salary_min_monthly=1_800))[-1] == "1900 €/mois"
