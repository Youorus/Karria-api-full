"""Freins par compte posés par l'audit du 2026-11-20 : exports et modification de la recherche."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from karria.modules.documents.api import dependencies as documents_deps
from karria.modules.offers.api import dependencies as offers_deps
from karria.modules.offers.api.schemas import LOCATION_MAX_CHARS, CriteriaRequest
from karria.shared.auth import Principal
from karria.shared.errors.exceptions import RateLimitedError


class TestFreinsParCompte:
    def test_les_exports_d_un_compte_sont_plafonnes_sans_gener_les_autres(self) -> None:
        someone = Principal(subject_id=uuid4())
        for _ in range(documents_deps.EXPORT_RATE_LIMIT):
            documents_deps.throttle_export(someone)

        with pytest.raises(RateLimitedError) as raised:
            documents_deps.throttle_export(someone)

        assert raised.value.details["reason"] == "export_rate_limited"
        documents_deps.throttle_export(Principal(subject_id=uuid4()))

    def test_les_modifications_de_recherche_d_un_compte_sont_plafonnees(self) -> None:
        someone = Principal(subject_id=uuid4())
        for _ in range(offers_deps.CRITERIA_RATE_LIMIT):
            offers_deps.throttle_criteria(someone)

        with pytest.raises(RateLimitedError) as raised:
            offers_deps.throttle_criteria(someone)

        assert raised.value.details["reason"] == "criteria_rate_limited"
        offers_deps.throttle_criteria(Principal(subject_id=uuid4()))


class TestCriteresBornes:
    def test_une_recherche_ordinaire_passe(self) -> None:
        body = CriteriaRequest(job_titles=["serveur"], location={"label": "Paris"})
        assert body.to_domain().job_titles == ["serveur"]

    def test_un_intitule_demesure_est_refuse(self) -> None:
        with pytest.raises(ValidationError):
            CriteriaRequest(job_titles=["x" * 121], whole_france=True)

    def test_un_lieu_demesure_est_refuse(self) -> None:
        with pytest.raises(ValidationError):
            CriteriaRequest(job_titles=["serveur"], location={"label": "x" * LOCATION_MAX_CHARS})
