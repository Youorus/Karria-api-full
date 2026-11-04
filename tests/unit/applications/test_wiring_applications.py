"""Les branchements d'`applications` sur `offers`, `cv` et `documents`, et ses ponts du bus."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from doubles import CollectingBus, sample_base_cv
from karria.bootstrap import wiring
from karria.bootstrap.app import create_app
from karria.bootstrap.wiring import (
    CvBaseCvReader,
    CvModuleValidator,
    DocumentsTemplateChecker,
    OffersOfferReader,
    applications_logo_url_from_offers,
    base_cv_reader_from_cv,
    cv_validator_from_cv,
    offer_reader_from_offers,
    subscribe_bridges,
    template_checker_from_documents,
)
from karria.modules.applications import (
    CvInvalidError,
    DossierApplied,
    DossierUnapplied,
    get_base_cv_reader,
    get_cv_validator,
    get_logo_url,
    get_offer_reader,
    get_template_checker,
)
from karria.modules.cv import CurriculumVitae, GetMyCv, Identity
from karria.modules.documents import PremiumTemplate, TemplateShop
from karria.modules.offers import GetOfferForUser
from karria.shared.config import AppSettings, Environment, Settings, StorageSettings
from karria.shared.errors import EntityNotFoundError
from karria.shared.storage import InMemoryObjectStorage

pytestmark = pytest.mark.unit


def test_les_cinq_ports_sont_branches() -> None:
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))

    assert app.dependency_overrides[get_offer_reader] is offer_reader_from_offers
    assert app.dependency_overrides[get_logo_url] is applications_logo_url_from_offers
    assert app.dependency_overrides[get_base_cv_reader] is base_cv_reader_from_cv
    assert app.dependency_overrides[get_cv_validator] is cv_validator_from_cv
    assert app.dependency_overrides[get_template_checker] is template_checker_from_documents


class TestOfferReader:
    async def test_traduit_l_offre_d_offers_et_rend_none_si_inconnue(self) -> None:
        offer_id = uuid4()
        company = SimpleNamespace(name="Lemaire", logo_hash="abc", website="https://l.example")
        offer = SimpleNamespace(
            id=offer_id,
            title="Vendeur",
            description="Texte",
            company=company,
            location=SimpleNamespace(label="Lyon 3e"),
            contract_type=SimpleNamespace(value="cdi", label="CDI"),
            apply_url=None,
            origin_url="https://o.example",
            published_at=datetime(2026, 11, 1, tzinfo=UTC),
        )

        class GetOffer:
            async def execute(self, user_id: object, wanted: object) -> Any:
                if wanted != offer_id:
                    raise EntityNotFoundError("Offre", wanted)
                return SimpleNamespace(offer=offer)

        reader = offer_reader_from_offers(cast("GetOfferForUser", GetOffer()))
        assert isinstance(reader, OffersOfferReader)
        card = await reader.read(offer_id)
        assert card is not None
        assert card.company == "Lemaire"
        assert card.logo_hash == "abc"
        assert card.city == "Lyon 3e"
        assert card.contract_label == "CDI"
        assert card.source_url == "https://o.example"
        assert await reader.read(uuid4()) is None

    async def test_une_offre_sans_entreprise_ni_lieu(self) -> None:
        offer = SimpleNamespace(
            id=uuid4(),
            title="X",
            description="",
            company=None,
            location=None,
            contract_type=None,
            apply_url="https://a.example",
            origin_url=None,
            published_at=None,
        )

        class GetOffer:
            async def execute(self, user_id: object, wanted: object) -> Any:
                return SimpleNamespace(offer=offer)

        card = await OffersOfferReader(cast("GetOfferForUser", GetOffer())).read(uuid4())
        assert card is not None
        assert card.company is None
        assert card.city is None
        assert card.source_url == "https://a.example"


def test_l_adresse_du_logo_vient_d_offers() -> None:
    url = applications_logo_url_from_offers(lambda digest, size: f"https://l/{digest}/{size}.webp")

    assert url("abc") == "https://l/abc/128.webp"


class TestBaseCvReader:
    async def test_rend_le_cv_en_json_ou_rien_s_il_est_vide(self) -> None:
        now = datetime(2026, 11, 5, tzinfo=UTC)
        filled = CurriculumVitae(
            id=uuid4(),
            user_id=uuid4(),
            created_at=now,
            updated_at=now,
            identity=Identity(first_name="Awa", last_name="Diallo"),
        )

        class Get:
            def __init__(self, cv: CurriculumVitae) -> None:
                self.cv = cv

            async def execute(self, user_id: object) -> CurriculumVitae:
                return self.cv

        reader = base_cv_reader_from_cv(cast("GetMyCv", Get(filled)))
        assert isinstance(reader, CvBaseCvReader)
        base = await reader.read(uuid4())
        assert base is not None
        assert base.data["identity"]["first_name"] == "Awa"
        assert base.updated_at == now

        blank = CurriculumVitae.empty(uuid4(), now=now)
        assert await CvBaseCvReader(cast("GetMyCv", Get(blank))).read(uuid4()) is None


class TestCvValidator:
    def test_normalise_un_cv_valide(self) -> None:
        validator = cv_validator_from_cv()
        assert isinstance(validator, CvModuleValidator)

        out = validator.validate({**sample_base_cv(), "inconnu": 1, "summary": "  ok  "})

        assert out["summary"] == "ok"
        assert out["identity"]["first_name"] == "Awa"
        assert len(out["experiences"]) == 2
        assert "inconnu" not in out

    def test_refuse_avec_le_champ_fautif(self) -> None:
        cv = sample_base_cv()
        cv["experiences"][1]["end_date"] = "2020-01"  # avant le début
        cv["contact"] = {"email": "", "phone": ""}
        cv["skills"] = "pas une liste"
        cv["summary"] = "x" * 1501

        with pytest.raises(CvInvalidError) as excinfo:
            cv_validator_from_cv().validate(cv)

        fields = {f["field"] for f in excinfo.value.details["fields"]}
        assert "experiences.1" in fields
        assert "contact" in fields
        assert "skills" in fields
        assert "summary" in fields


class TestTemplateChecker:
    async def test_possede_ou_gratuit(self) -> None:
        user, owned, premium, free = uuid4(), uuid4(), uuid4(), uuid4()

        class Shop:
            async def owns(self, who: object, template_id: object) -> bool:
                return who == user and template_id == owned

            async def premium_offer(self, template_id: object) -> PremiumTemplate | None:
                if template_id in {premium, owned}:
                    return PremiumTemplate(cast("Any", template_id), "Pro", cast("Any", 4))
                return None

        checker = template_checker_from_documents(cast("TemplateShop", Shop()))
        assert isinstance(checker, DocumentsTemplateChecker)
        assert await checker.usable(user, owned)
        assert await checker.usable(user, free)
        assert not await checker.usable(user, premium)
        assert not await checker.usable(uuid4(), owned)


async def test_les_ponts_relaient_la_candidature_au_fil(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[object, object, bool | None]] = []

    class Update:
        async def execute(self, user_id: object, offer_id: object, changes: Any) -> None:
            seen.append((user_id, offer_id, changes.applied))

    monkeypatch.setattr(wiring, "build_update_state", Update)
    bus = CollectingBus()
    subscribe_bridges(bus, storage=InMemoryObjectStorage(StorageSettings()))
    user, offer, dossier = uuid4(), uuid4(), uuid4()

    await bus.publish(DossierApplied(user_id=user, offer_id=offer, dossier_id=dossier))
    await bus.publish(DossierUnapplied(user_id=user, offer_id=offer, dossier_id=dossier))

    assert seen == [(user, offer, True), (user, offer, False)]
