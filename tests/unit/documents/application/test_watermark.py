"""Le filigrane (ADR-0040) : pas les trois premiers CV du mois, jamais chez un abonné, sans jamais bloquer."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from documents.application.conftest import Env
from documents.fakes import NOW
from documents.infrastructure.fakes import cv_font_set
from documents.infrastructure.test_header_layout import a_spec, a_view, stacked_right
from doubles import FakeClock
from karria.modules.documents.application import ExportCv, ReadExportAllowance
from karria.modules.documents.domain.templates import DocumentKind, GeneratedCv
from karria.modules.documents.domain.watermark import (
    ALWAYS_WATERMARKED,
    FREE_CLEAN_EXPORTS_PER_MONTH,
    GENERATION_WINDOW,
    ExportAllowance,
    generation_starts,
    month_start,
)
from karria.modules.documents.infrastructure.footer import footer_text
from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer

pytestmark = pytest.mark.unit


class Subscriptions:
    def __init__(self, *subscribers: UUID) -> None:
        self.subscribers = set(subscribers)

    async def is_subscriber(self, user_id: UUID) -> bool:
        return user_id in self.subscribers


def allowance(env: Env, subscriptions: Subscriptions | None = None) -> ReadExportAllowance:
    return ReadExportAllowance(
        generated=env.generated, subscriptions=subscriptions or Subscriptions(), clock=env.clock
    )


def export(env: Env, subscriptions: Subscriptions | None = None) -> ExportCv:
    return ExportCv(
        renderer=env.renderer(),
        templates=env.templates,
        owned=env.owned,
        generated=env.generated,
        engine=env.engine,
        storage=env.storage,
        jobs=env.jobs,
        clock=env.clock,
        allowance=allowance(env, subscriptions),
    )


def later(env: Env, *, minutes: int = 60) -> None:
    """Avance l'horloge : une heure plus tard, c'est un autre CV."""
    env.clock = FakeClock(env.clock.now() + timedelta(minutes=minutes))


async def one(
    env: Env, template_id: UUID, subscriptions: Subscriptions | None = None, *, wait: int = 60
) -> GeneratedCv:
    """Un export, `wait` minutes après le précédent."""
    later(env, minutes=wait)
    return await export(env, subscriptions).execute(
        env.user, template_id=template_id, formats=["pdf"], ats_safe=None, offer_id=None
    )


class TestRegle:
    def test_le_mois_civil_commence_le_premier_a_minuit_utc(self) -> None:
        assert month_start(datetime(2026, 11, 20, 15, 4, tzinfo=UTC)) == datetime(
            2026, 11, 1, tzinfo=UTC
        )

    def test_trois_offerts_puis_le_filigrane(self) -> None:
        assert FREE_CLEAN_EXPORTS_PER_MONTH == 3
        assert ExportAllowance(subscriber=False, used=0).remaining == 3
        assert not ExportAllowance(subscriber=False, used=2).watermark
        assert ExportAllowance(subscriber=False, used=3).watermark
        assert ExportAllowance(subscriber=False, used=7).remaining == 0

    def test_un_abonne_na_jamais_de_filigrane(self) -> None:
        assert not ExportAllowance(subscriber=True, used=40).watermark

    def test_sans_compteur_branche_le_filigrane_reste(self) -> None:
        assert ALWAYS_WATERMARKED.watermark


class TestGeneration:
    def test_des_exports_rapproches_sont_une_seule_generation(self) -> None:
        start = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
        exports = [start, start + timedelta(minutes=5), start + timedelta(minutes=29)]
        assert generation_starts(exports) == [start]
        # Au-delà de la demi-heure qui suit le premier, c'est un autre CV.
        other = start + GENERATION_WINDOW + timedelta(minutes=1)
        assert generation_starts([*exports, other]) == [start, other]

    def test_la_troisieme_generation_reste_sans_filigrane_tant_quelle_dure(self) -> None:
        day = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
        exports = [day - timedelta(days=2), day - timedelta(days=1), day]
        during = ExportAllowance.at(day + timedelta(minutes=10), subscriber=False, exports=exports)
        after = ExportAllowance.at(day + timedelta(minutes=45), subscriber=False, exports=exports)
        assert (during.used, during.watermark) == (3, False)
        assert (after.used, after.watermark) == (3, True)


class TestExport:
    async def test_les_trois_premiers_cv_du_mois_sortent_sans_filigrane(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)

        first = [await one(env, template.id) for _ in range(3)]
        fourth = await one(env, template.id)

        assert [g.watermarked for g in first] == [False, False, False]
        assert fourth.watermarked is True
        # Rien n'est bloqué : le quatrième CV existe, avec son fichier.
        assert fourth.pdf_key is not None
        views = [call["view"] for name, call in env.engine.calls if name == "render_pdf"]
        assert [v["watermark"] for v in views] == [False, False, False, True]

    async def test_retoucher_le_meme_cv_ne_consomme_quune_generation(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)

        # Finaliser, Optimiser, retour de l'atelier : trois fichiers, un seul CV.
        retouches = [await one(env, template.id, wait=4) for _ in range(6)]
        state = await allowance(env).execute(env.user)

        assert not any(g.watermarked for g in retouches)
        assert state.used == 1
        assert state.remaining == 2

    async def test_un_cv_filigrane_nentame_rien(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        for _ in range(5):
            await one(env, template.id)
        later(env)

        state = await allowance(env).execute(env.user)

        assert state.used == 3
        assert state.watermark

    async def test_le_compteur_repart_au_mois_suivant(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        for _ in range(3):
            await one(env, template.id)
        later(env)
        assert (await allowance(env).execute(env.user)).watermark

        env.clock = FakeClock(NOW.replace(month=NOW.month % 12 + 1, day=2))

        assert not (await allowance(env).execute(env.user)).watermark
        assert (await one(env, template.id)).watermarked is False

    async def test_un_abonne_exporte_sans_filigrane_au_dela_de_trois(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)

        exports = [await one(env, template.id, Subscriptions(env.user)) for _ in range(5)]

        assert not any(g.watermarked for g in exports)

    async def test_sabonner_retire_le_filigrane_du_prochain_export(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        for _ in range(3):
            await one(env, template.id)
        assert (await one(env, template.id)).watermarked is True

        after = await one(env, template.id, Subscriptions(env.user))

        assert after.watermarked is False

    async def test_les_lettres_et_les_autres_comptes_ne_comptent_pas(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)
        generated = await one(env, template.id)
        far = generated.created_at + timedelta(hours=5)
        await env.generated.add(
            replace(generated, id=generated.new_id(), kind=DocumentKind.LETTER, created_at=far)
        )
        await env.generated.add(
            replace(generated, id=generated.new_id(), user_id=generated.new_id(), created_at=far)
        )

        assert (await allowance(env).execute(env.user)).used == 1

    async def test_sans_compteur_lexport_garde_le_filigrane(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.cvs.put(env.user)

        generated = await env.export().execute(
            env.user, template_id=template.id, formats=["pdf"], ats_safe=None, offer_id=None
        )

        assert generated.watermarked is True


class TestPiedDePage:
    def test_les_moteurs_ne_posent_la_ligne_que_si_la_vue_la_demande(self) -> None:

        fonts = cv_font_set()
        line = footer_text("cv").split(" · ")[0]
        clean = HtmlRenderer(
            a_spec(stacked_right()), {**a_view(), "watermark": False}, fonts, photo=None
        ).render()
        marked = HtmlRenderer(
            a_spec(stacked_right()), {**a_view(), "watermark": True}, fonts, photo=None
        ).render()
        default = HtmlRenderer(a_spec(stacked_right()), a_view(), fonts, photo=None).render()

        assert line not in clean
        assert line in marked
        assert line in default  # une vue qui ne dit rien garde le filigrane
