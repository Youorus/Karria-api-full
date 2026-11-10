"""La lettre sobre pour la galerie : aperçus déposés par la commande, lus une fois par processus (ADR-0036)."""

from __future__ import annotations

import json

import pytest

from documents.application.conftest import Env
from documents.fakes import PDF_LETTER_BYTES, PNG_BYTES, make_letter_spec
from karria.modules.documents.api.schemas import TemplateRead
from karria.modules.documents.application import PublishPlainLetterPreview
from karria.modules.documents.application.use_cases import (
    PLAIN_LETTER_PREFIX,
    _plain_letter_cache,
    plain_letter_keys,
    plain_letter_pages,
)
from karria.shared.storage import Bucket

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    _plain_letter_cache["until"] = 0.0
    _plain_letter_cache["pages"] = None


class TestPublication:
    async def test_la_lettre_sobre_est_rendue_avec_son_exemple_et_ses_apercus_deposes(
        self, env: Env
    ) -> None:
        meta = await PublishPlainLetterPreview(env.engine, env.storage, env.clock).execute()

        [render] = env.engine.called("render_pdf")
        assert render["view"]["kind"] == "letter"
        assert render["view"]["header"]["full_name"] == "Camille Marchand"
        assert render["view"]["date_line"] == "Lyon, le 6 novembre 2026"
        assert render["spec"]["source"] == {"producer": "karria"}
        keys = plain_letter_keys()
        for key in (keys["thumbnail_key"], keys["preview_key"], keys["full_key"]):
            assert key.startswith(PLAIN_LETTER_PREFIX)
            assert await env.storage.exists(Bucket.TEMPLATES, key)
        assert await env.storage.get(Bucket.TEMPLATES, keys["preview_key"]) == PNG_BYTES
        assert (
            await env.storage.get(Bucket.TEMPLATES, f"{PLAIN_LETTER_PREFIX}/sample_render.pdf")
            == PDF_LETTER_BYTES
        )
        stored = json.loads(await env.storage.get(Bucket.TEMPLATES, keys["meta_key"]))
        assert stored == meta
        assert meta["blurhash"] == "LKO2?U%2Tw=w"
        assert meta["version"] == 1


class TestLectureParLaGalerie:
    async def test_sans_commande_passee_aucune_page_et_le_resultat_est_garde(
        self, env: Env
    ) -> None:
        assert await plain_letter_pages(env.storage) is None
        # Déposé après coup : la galerie ne le voit qu'à l'expiration du cache.
        await PublishPlainLetterPreview(env.engine, env.storage, env.clock).execute()
        _plain_letter_cache["until"] = float("inf")
        _plain_letter_cache["pages"] = None
        assert await plain_letter_pages(env.storage) is None
        _plain_letter_cache["until"] = 0.0
        pages = await plain_letter_pages(env.storage)
        assert pages is not None
        assert pages["blurhash"] == "LKO2?U%2Tw=w"
        assert pages["thumbnail_key"] == f"{PLAIN_LETTER_PREFIX}/vignette.webp"

    async def test_la_galerie_montre_la_lettre_sobre_sinon_la_lettre_assortie(
        self, env: Env
    ) -> None:
        plain, plain_version = env.templates.publish(name="plain")
        paired, paired_version = env.templates.publish(
            name="paired", letter_spec=make_letter_spec()
        )
        pages = {**plain_letter_keys(), "blurhash": "SOBRE"}

        read_plain = await TemplateRead.present(
            plain, plain_version, env.storage, detail=True, plain_letter=pages
        )
        read_paired = await TemplateRead.present(
            paired, paired_version, env.storage, detail=True, plain_letter=pages
        )
        read_none = await TemplateRead.present(plain, plain_version, env.storage, detail=True)

        assert read_plain.has_letter is False
        [letter] = read_plain.cover_letters
        assert (letter.id, letter.style, letter.label) == ("plain", "plain", "Lettre sobre")
        assert letter.thumbnail_blurhash == "SOBRE"
        assert letter.signed_full_url is not None
        assert PLAIN_LETTER_PREFIX in letter.signed_full_url
        assert read_plain.pages is not None
        assert [(p.kind, p.label, p.style) for p in read_plain.pages] == [
            ("cv", "CV", None),
            ("letter", "Lettre sobre", "plain"),
        ]

        assert read_paired.has_letter is True
        [letter] = read_paired.cover_letters
        assert (letter.id, letter.style, letter.label) == (
            "template",
            "template",
            "Lettre assortie",
        )
        assert letter.thumbnail_blurhash == "LLETTRE"
        assert letter.signed_thumbnail_url is not None
        assert "/letter/vignette.webp" in letter.signed_thumbnail_url
        assert read_paired.pages is not None
        assert read_paired.pages[1].label == "Lettre assortie"

        assert read_none.cover_letters == []
        assert read_none.pages is not None
        assert len(read_none.pages) == 1
