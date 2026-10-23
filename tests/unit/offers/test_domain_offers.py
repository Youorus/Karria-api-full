"""L'offre normalisée : ses propriétés dérivées et l'UUID v7."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from karria.modules.offers.domain import (
    CONTRACT_LABELS,
    Company,
    ContractType,
    ExperienceLevel,
    Location,
    Offer,
    Salary,
    Source,
    uuid7,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, 8, tzinfo=UTC)


def make_offer(**changes: object) -> Offer:
    offer = Offer(
        id=uuid7(now_ms=1_000),
        source=Source.INDEED,
        source_id="in-1",
        title="Serveur H/F",
        company=Company(name="Le Bistrot"),
        description="Service en salle.",
        location=Location(label="Paris", city="Paris", latitude=48.85, longitude=2.35),
        contract_type=ContractType.CDI,
        remote=None,
        schedule=None,
        experience=None,
        salary=Salary(min_monthly=None, max_monthly=None, raw=None),
        published_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        apply_url=None,
        origin_url=None,
        content_hash="h1",
    )
    return dataclasses.replace(offer, **changes)  # type: ignore[arg-type]


class TestOffre:
    def test_une_offre_active_et_canonique_est_indexable(self) -> None:
        assert make_offer().indexable

    def test_un_doublon_nest_pas_indexable(self) -> None:
        offer = make_offer(duplicate_of=uuid7())
        assert not offer.is_canonical
        assert not offer.indexable

    def test_une_offre_inactive_nest_pas_indexable(self) -> None:
        assert not make_offer(is_active=False).indexable

    def test_lalternance_se_lit_sur_le_contrat(self) -> None:
        assert make_offer(contract_type=ContractType.ALTERNANCE).is_alternance
        assert not make_offer().is_alternance

    def test_une_offre_sans_version_nest_pas_indexee(self) -> None:
        assert not make_offer().is_indexed
        assert make_offer(indexed_version="m@t2").is_indexed

    def test_le_plongement_vaut_a_meme_version_et_meme_contenu(self) -> None:
        offer = make_offer(indexed_version="m@t2", indexed_hash="h1")
        assert offer.embedding_current("m@t2")

    def test_un_contenu_modifie_impose_un_nouveau_plongement(self) -> None:
        offer = make_offer(indexed_version="m@t2", indexed_hash="ancien")
        assert not offer.embedding_current("m@t2")

    def test_un_autre_modele_impose_un_nouveau_plongement(self) -> None:
        offer = make_offer(indexed_version="m@t1", indexed_hash="h1")
        assert not offer.embedding_current("m@t2")


class TestValeurs:
    def test_chaque_contrat_a_son_libelle(self) -> None:
        assert {kind.label for kind in ContractType} == set(CONTRACT_LABELS.values())
        assert ContractType.INTERIM.label == "Intérim"

    def test_les_niveaux_dexperience_sont_ordonnes(self) -> None:
        ranks = [level.rank for level in ExperienceLevel]
        assert ranks == sorted(ranks)
        assert ExperienceLevel.BEGINNER.rank < ExperienceLevel.SENIOR.rank

    def test_un_lieu_sans_coordonnees_nest_pas_geocode(self) -> None:
        assert not Location(label="Lyon").is_geocoded
        assert Location(label="Lyon", latitude=45.7, longitude=4.8).is_geocoded

    def test_un_salaire_est_connu_des_quune_borne_lest(self) -> None:
        assert not Salary(min_monthly=None, max_monthly=None, raw="selon profil").is_known
        assert Salary(min_monthly=None, max_monthly=2_000, raw=None).is_known


class TestUuid7:
    def test_la_version_et_la_variante_sont_celles_de_la_rfc(self) -> None:
        value = uuid7(now_ms=1_700_000_000_000)
        assert value.version == 7
        assert value.variant == "specified in RFC 4122"

    def test_lhorodatage_ordonne_les_identifiants(self) -> None:
        assert uuid7(now_ms=1_000) < uuid7(now_ms=2_000)

    def test_lhorloge_systeme_sert_par_defaut(self) -> None:
        assert uuid7().version == 7
