"""Galerie, possession et choix : ce qu'on voit, ce qu'on possède, ce qu'on choisit."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from documents.application.conftest import Env
from documents.fakes import NOW, make_spec, make_template, make_version
from karria.modules.documents.application import (
    AcquireFreeTemplate,
    GetSelection,
    GetTemplate,
    ListOwnedTemplates,
    ListTemplates,
    RecordTemplateView,
    SelectTemplate,
)
from karria.modules.documents.domain.errors import (
    TemplateLockedError,
    TemplateNotFoundError,
    TemplateNotFreeError,
)

pytestmark = pytest.mark.unit


class TestGalerie:
    async def test_la_galerie_ne_montre_que_les_modeles_publies_filtres(self, env: Env) -> None:
        free, _ = env.templates.publish(name="libre")
        pro, _ = env.templates.publish(name="pro", premium=True)
        brouillon = make_template(name="brouillon")
        await env.templates.save(brouillon)

        tous = await ListTemplates(env.templates).execute()
        pros = await ListTemplates(env.templates).execute(premium=True)

        assert {t.id for t in tous} == {free.id, pro.id}
        assert [t.id for t in pros] == [pro.id]

    async def test_un_modele_publie_se_lit_avec_sa_version_publiee(self, env: Env) -> None:
        template, version = env.templates.publish()

        found, found_version = await GetTemplate(env.templates).execute(template.id)

        assert (found, found_version) == (template, version)

    async def test_un_modele_inconnu_n_existe_pas(self, env: Env) -> None:
        with pytest.raises(TemplateNotFoundError):
            await GetTemplate(env.templates).execute(uuid4())

    async def test_un_modele_sans_version_publiee_n_existe_pas(self, env: Env) -> None:
        template = make_template()
        await env.templates.save(template)

        with pytest.raises(TemplateNotFoundError):
            await GetTemplate(env.templates).execute(template.id)

    async def test_une_version_publiee_introuvable_rend_le_modele_introuvable(
        self, env: Env
    ) -> None:
        template = make_template(published_version_id=uuid4(), published_version=1)
        await env.templates.save(template)

        with pytest.raises(TemplateNotFoundError):
            await GetTemplate(env.templates).execute(template.id)

    async def test_une_version_sans_specification_rend_le_modele_introuvable(
        self, env: Env
    ) -> None:
        template = make_template()
        version = make_version(template.id, spec=None)
        await env.templates.save(replace(template, published_version_id=version.id))
        await env.templates.save_version(version)

        with pytest.raises(TemplateNotFoundError):
            await GetTemplate(env.templates).execute(template.id)

    async def test_une_vue_du_detail_est_comptee(self, env: Env) -> None:
        template, _ = env.templates.publish()

        await RecordTemplateView(env.templates).execute(template.id)

        assert env.templates.templates[template.id].views_count == 1

    async def test_une_vue_d_un_modele_non_publie_est_refusee(self, env: Env) -> None:
        with pytest.raises(TemplateNotFoundError):
            await RecordTemplateView(env.templates).execute(uuid4())


class TestPossession:
    async def test_un_modele_gratuit_s_ajoute_aux_modeles_de_la_personne(self, env: Env) -> None:
        template, _ = env.templates.publish()

        ownership = await AcquireFreeTemplate(env.templates, env.owned, env.clock).execute(
            env.user, template.id
        )

        assert ownership.template_id == template.id
        assert ownership.source == "free"
        assert ownership.acquired_at == NOW
        assert await ListOwnedTemplates(env.owned).execute(env.user) == [ownership]

    async def test_l_ajout_d_un_modele_deja_possede_est_idempotent(self, env: Env) -> None:
        autre, _ = env.templates.publish(name="autre")
        template, _ = env.templates.publish()
        env.owned.give(env.user, autre.id)
        existing = env.owned.give(env.user, template.id, source="free")

        again = await AcquireFreeTemplate(env.templates, env.owned, env.clock).execute(
            env.user, template.id
        )

        assert again == existing
        assert len(env.owned.ownerships) == 2

    async def test_un_modele_pro_ne_s_ajoute_pas_gratuitement(self, env: Env) -> None:
        template, _ = env.templates.publish(premium=True)

        with pytest.raises(TemplateNotFreeError):
            await AcquireFreeTemplate(env.templates, env.owned, env.clock).execute(
                env.user, template.id
            )


class TestChoix:
    async def test_sans_choix_la_selection_est_vide(self, env: Env) -> None:
        assert await GetSelection(env.owned).execute(env.user) is None

    async def test_le_choix_d_un_modele_est_garde_avec_la_variante_ats(self, env: Env) -> None:
        template, _ = env.templates.publish()

        selection = await SelectTemplate(env.templates, env.owned, env.clock).execute(
            env.user, template.id, ats_safe=True
        )

        assert selection.template_id == template.id
        assert selection.ats_safe is True
        assert selection.updated_at == NOW
        assert await GetSelection(env.owned).execute(env.user) == selection

    async def test_un_modele_pro_non_possede_ne_se_choisit_pas(self, env: Env) -> None:
        template, _ = env.templates.publish(premium=True)

        with pytest.raises(TemplateLockedError):
            await SelectTemplate(env.templates, env.owned, env.clock).execute(
                env.user, template.id, ats_safe=False
            )

    async def test_un_modele_pro_possede_se_choisit(self, env: Env) -> None:
        template, _ = env.templates.publish(premium=True, spec=make_spec())
        env.owned.give(env.user, template.id)

        selection = await SelectTemplate(env.templates, env.owned, env.clock).execute(
            env.user, template.id, ats_safe=False
        )

        assert selection.template_id == template.id
