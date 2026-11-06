"""L'export d'une lettre : PDF et DOCX dans le modèle, un export `kind: letter` (ADR-0030)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from documents.application.conftest import Env
from documents.fakes import DOCX_BYTES, NOW, PDF_LETTER_BYTES, FakeLogoFetcher, make_letter_spec
from doubles import sample_image
from karria.modules.documents.application import ExportLetter
from karria.modules.documents.domain.errors import (
    InvalidFormatError,
    TemplateLockedError,
    TemplateNotFoundError,
)
from karria.modules.documents.domain.templates import (
    DocumentKind,
    ExplanationStatus,
    Fit,
    Selection,
)
from karria.shared.storage import Bucket

pytestmark = pytest.mark.unit

LETTER: dict[str, Any] = {
    "recipient": {"company": "Acme", "logo_url": "https://storage.karria.fr/logos/x/128.webp"},
    "subject": "Candidature au poste de vendeuse",
    "paragraphs": [{"kind": "libre", "text": "Un."}, {"kind": "libre", "text": "Deux."}],
    "closing": "Cordialement.",
    "sender": {"first_name": "Awa", "last_name": "Diallo"},
}


def _export(env: Env, logos: FakeLogoFetcher | None = None) -> ExportLetter:
    return ExportLetter(
        templates=env.templates,
        owned=env.owned,
        generated=env.generated,
        engine=env.engine,
        storage=env.storage,
        logos=logos or FakeLogoFetcher(),
        clock=env.clock,
    )


class TestExportLettre:
    async def test_les_deux_fichiers_sont_ranges_et_l_export_est_une_lettre(self, env: Env) -> None:
        template, version = env.templates.publish()
        env.engine.fit = Fit(spacing=0.9, font_scale=1.0, pages=1)
        logos = FakeLogoFetcher(logos={LETTER["recipient"]["logo_url"]: sample_image()})
        dossier = uuid4()

        generated = await _export(env, logos).execute(
            env.user, template_id=template.id, letter=LETTER, formats=None, dossier_id=dossier
        )

        assert generated.kind is DocumentKind.LETTER
        assert generated.dossier_id == dossier
        assert generated.company_name == "Acme"
        assert generated.full_name == "Awa Diallo"
        assert generated.template_version_id == version.id
        assert generated.ats == {}
        assert generated.explanation_status is ExplanationStatus.UNAVAILABLE
        assert generated.fit == Fit(spacing=0.9, font_scale=1.0, pages=1)
        assert generated.created_at == NOW
        assert generated.pdf_key == f"generated-letters/{env.user}/{generated.id}/lettre.pdf"
        assert generated.docx_key == f"generated-letters/{env.user}/{generated.id}/lettre.docx"
        assert await env.storage.get(Bucket.DOCUMENTS, generated.pdf_key) == PDF_LETTER_BYTES
        assert await env.storage.get(Bucket.DOCUMENTS, generated.docx_key) == DOCX_BYTES
        assert env.generated.items[generated.id] == generated
        assert env.templates.templates[template.id].uses_count == 1
        [pdf] = env.engine.called("render_pdf")
        assert pdf["view"]["kind"] == "letter"
        assert pdf["assets"]["logo.png"].startswith(b"\x89PNG")
        [docx] = env.engine.called("render_docx")
        assert docx["fit"] == generated.fit
        assert env.jobs.explanations == []

    async def test_le_pdf_seul_et_sans_logo(self, env: Env) -> None:
        template, _ = env.templates.publish()
        letter = {**LETTER, "recipient": {"company": "Acme"}}

        generated = await _export(env).execute(
            env.user, template_id=template.id, letter=letter, formats=["pdf"], dossier_id=None
        )

        assert generated.docx_key is None
        assert env.engine.called("render_docx") == []
        assert "logo.png" not in env.engine.called("render_pdf")[0]["assets"]

    async def test_sans_modele_le_choix_de_la_personne_sert(self, env: Env) -> None:
        template, _ = env.templates.publish()
        env.owned.selections[env.user] = Selection(
            user_id=env.user, template_id=template.id, ats_safe=True, updated_at=NOW
        )
        generated = await _export(env).execute(
            env.user, template_id=None, letter=LETTER, formats=None, dossier_id=None
        )
        assert generated.template_id == template.id
        assert generated.ats_safe is False

    async def test_sans_modele_ni_choix_rien_ne_s_exporte(self, env: Env) -> None:
        with pytest.raises(TemplateNotFoundError):
            await _export(env).execute(
                env.user, template_id=None, letter=LETTER, formats=None, dossier_id=None
            )

    async def test_un_modele_pro_non_possede_et_un_format_inconnu_sont_refuses(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish(premium=True)
        with pytest.raises(TemplateLockedError):
            await _export(env).execute(
                env.user, template_id=template.id, letter=LETTER, formats=None, dossier_id=None
            )
        with pytest.raises(InvalidFormatError):
            await _export(env).execute(
                env.user, template_id=template.id, letter=LETTER, formats=["odt"], dossier_id=None
            )


class TestCouleurDeLaLettre:
    async def test_la_lettre_assortie_suit_la_couleur_du_dossier(self, env: Env) -> None:
        template, _ = env.templates.publish(letter_spec=make_letter_spec(accent="#1A5276"))

        generated = await _export(env).execute(
            env.user,
            template_id=template.id,
            letter=LETTER,
            formats=["pdf"],
            dossier_id=None,
            primary_color="#7B1E2B",
        )

        assert generated.primary_color == "#7B1E2B"
        assert generated.letter_style == "template"
        [call] = env.engine.called("render_pdf")
        assert call["spec"]["styles"]["section_title"]["color"] == "#7B1E2B"


class TestStyleDeLettre:
    """ADR-0036 : la lettre assortie du modèle, ou la lettre sobre ; l'export dit le style servi."""

    async def test_sans_lettre_assortie_la_sobre_est_servie_quoi_qu_on_demande(
        self, env: Env
    ) -> None:
        template, _ = env.templates.publish()
        for asked in (None, "template", "plain"):
            generated = await _export(env).execute(
                env.user,
                template_id=template.id,
                letter=LETTER,
                formats=["pdf"],
                dossier_id=None,
                letter_style=asked,
            )
            assert generated.letter_style == "plain"
        for call in env.engine.called("render_pdf"):
            assert call["spec"]["source"] == {"producer": "karria"}
            assert call["spec"]["styles"]["paragraph"]["family"] == "Carlito"

    async def test_avec_une_lettre_assortie_le_choix_est_respecte(self, env: Env) -> None:
        template, version = env.templates.publish(letter_spec=make_letter_spec())
        served = []
        for asked in (None, "template", "plain"):
            generated = await _export(env).execute(
                env.user,
                template_id=template.id,
                letter=LETTER,
                formats=["pdf"],
                dossier_id=None,
                letter_style=asked,
            )
            served.append(generated.letter_style)
        assert served == ["template", "template", "plain"]
        first, second, third = env.engine.called("render_pdf")
        assert first["spec"] == version.letter_spec
        assert second["spec"] == version.letter_spec
        assert third["spec"]["source"] == {"producer": "karria"}
