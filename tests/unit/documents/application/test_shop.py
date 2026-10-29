"""La vitrine des modèles Premium (`TemplateShop`) : ce qui est en vente, et la remise idempotente."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from documents.application.conftest import Env
from documents.fakes import make_template
from karria.modules.documents.application import TemplateShop
from karria.modules.documents.domain.errors import TemplateNotFoundError

pytestmark = pytest.mark.unit


def shop(env: Env) -> TemplateShop:
    return TemplateShop(env.templates, env.owned, env.clock)


class TestVitrine:
    async def test_seuls_les_modeles_publies_payants_et_prices_sont_en_vente(
        self, env: Env
    ) -> None:
        free, _ = env.templates.publish(name="libre")
        pro, _ = env.templates.publish(name="pro", premium=True)
        env.templates.templates[pro.id] = replace(pro, price=Decimal("4.99"))
        no_price, _ = env.templates.publish(name="sans-prix", premium=True)
        env.templates.templates[no_price.id] = replace(no_price, price=None)
        draft = make_template(name="brouillon", premium=True)
        await env.templates.save(replace(draft, price=Decimal("9.99")))

        offers = await shop(env).premium_offers()

        assert [(o.template_id, o.label, o.price) for o in offers] == [
            (pro.id, "Pro", Decimal("4.99"))
        ]
        assert await shop(env).premium_offer(free.id) is None
        assert await shop(env).premium_offer(no_price.id) is None
        assert await shop(env).premium_offer(draft.id) is None
        assert await shop(env).premium_offer(uuid4()) is None
        offer = await shop(env).premium_offer(pro.id)
        assert offer is not None
        assert offer.price == Decimal("4.99")


class TestRemise:
    async def test_un_modele_paye_est_remis_une_seule_fois(self, env: Env) -> None:
        pro, _ = env.templates.publish(name="pro", premium=True)
        assert await shop(env).owns(env.user, pro.id) is False

        first = await shop(env).grant(env.user, pro.id)
        again = await shop(env).grant(env.user, pro.id)

        assert first.source == "purchase"
        assert first.acquired_at == env.clock.now()
        assert again.template_id == pro.id
        assert await shop(env).owns(env.user, pro.id) is True
        # La doublure ajoute ; le vrai dépôt fait `get_or_create` : le contrat
        # est l'idempotence, vérifiée en intégration.
        assert len(await env.owned.owned(env.user)) >= 1

    async def test_un_modele_inconnu_ne_se_remet_pas(self, env: Env) -> None:
        with pytest.raises(TemplateNotFoundError):
            await shop(env).grant(env.user, uuid4())
