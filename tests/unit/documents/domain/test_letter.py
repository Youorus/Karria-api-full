"""La lettre de motivation : date, nom de fichier, objet explicite, avertissements, vue et chemins (ADR-0030)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from karria.modules.documents.domain.letter import (
    DEFAULT_CLOSING,
    DEFAULT_GREETING,
    MAX_BODY_CHARS,
    explicit_subject,
    format_date_fr,
    from_letter,
    letter_file_name,
    letter_warnings,
    place_and_date,
)

pytestmark = pytest.mark.unit

LETTER: dict[str, Any] = {
    "recipient": {"company": "Acme", "city": "Lyon", "logo_url": None},
    "subject": "Candidature au poste de vendeuse",
    "paragraphs": [
        {"kind": "interet_entreprise", "text": "Acme m'attire."},
        {"kind": "competences", "text": "Je sais vendre."},
        {"kind": "libre", "text": "   "},
        {"kind": "pourquoi_moi", "text": "Je suis motivée."},
    ],
    "closing": "Cordialement.",
    "sender": {
        "first_name": "Awa",
        "last_name": "Diallo",
        "headline": "Vendeuse",
        "email": "awa@example.com",
        "phone": "06 12",
        "city": "Montreuil",
    },
}


class TestDateEtNoms:
    def test_la_date_se_lit_en_francais_avec_le_premier_en_ordinal(self) -> None:
        assert format_date_fr(date(2026, 11, 5)) == "5 novembre 2026"
        assert format_date_fr(date(2027, 3, 1)) == "1er mars 2027"

    def test_le_lieu_precede_la_date_quand_il_existe(self) -> None:
        assert place_and_date("Montreuil", date(2026, 11, 5)) == "Montreuil, le 5 novembre 2026"
        assert place_and_date("  ", date(2026, 11, 5)) == "Le 5 novembre 2026"
        assert place_and_date(None, date(2026, 11, 5)) == "Le 5 novembre 2026"

    def test_le_fichier_porte_l_entreprise(self) -> None:
        assert letter_file_name("Acme", "pdf") == "Lettre de motivation - Acme.pdf"
        assert letter_file_name("", "docx") == "Lettre de motivation.docx"
        assert letter_file_name(None, "pdf") == "Lettre de motivation.pdf"

    def test_l_objet_est_propose_quand_il_manque(self) -> None:
        assert explicit_subject("Candidature spontanée") == "Candidature spontanée"
        assert explicit_subject("", "vendeuse") == "Candidature au poste de vendeuse"
        assert explicit_subject(None) == ""


class TestAvertissements:
    def test_une_lettre_conforme_n_en_a_aucun(self) -> None:
        assert letter_warnings(LETTER) == []

    def test_chaque_ecart_aux_usages_est_nomme(self) -> None:
        bare = {
            "recipient": {},
            "subject": "Bonjour",
            "paragraphs": [{"kind": "libre", "text": "Un seul."}],
            "closing": "",
        }
        assert letter_warnings(bare) == [
            "too_few_paragraphs",
            "subject_not_explicit",
            "closing_missing",
            "company_missing",
        ]

    def test_trop_de_paragraphes_ou_trop_long(self) -> None:
        many = {**LETTER, "paragraphs": [{"kind": "libre", "text": "x"}] * 6}
        assert "too_many_paragraphs" in letter_warnings(many)
        long = {**LETTER, "paragraphs": [{"kind": "libre", "text": "a" * (MAX_BODY_CHARS + 1)}]}
        assert "body_too_long" in letter_warnings(long)


class TestVue:
    def test_la_vue_porte_les_textes_et_les_chemins_a_cote(self) -> None:
        view = from_letter({}, LETTER, today=date(2026, 11, 5))

        assert view["kind"] == "letter"
        assert view["header"]["full_name"] == "Awa Diallo"
        assert view["header"]["headline"] == "Vendeuse"
        assert view["recipient"] == {"company": "Acme", "city": "Lyon"}
        assert view["date_line"] == "Montreuil, le 5 novembre 2026"
        assert view["subject"] == "Candidature au poste de vendeuse"
        assert view["greeting"] == DEFAULT_GREETING
        # Le paragraphe vide est écarté, et son chemin avec lui.
        assert [p["text"] for p in view["paragraphs"]] == [
            "Acme m'attire.",
            "Je sais vendre.",
            "Je suis motivée.",
        ]
        assert view["paths"]["paragraphs"] == [
            "paragraphs.0.text",
            "paragraphs.1.text",
            "paragraphs.3.text",
        ]
        assert view["paths"]["header"]["full_name"] == "sender.full_name"
        assert view["paths"]["recipient"]["company"] == "recipient.company"
        assert view["signature"] == "Awa Diallo"
        assert view["sections"] == []

    def test_sans_politesse_ni_appel_les_formules_sobres_sont_posees(self) -> None:
        view = from_letter(
            {}, {**LETTER, "closing": None, "greeting": None}, today=date(2026, 1, 1)
        )
        assert view["closing"] == DEFAULT_CLOSING
        assert view["greeting"] == DEFAULT_GREETING
        assert view["date_line"] == "Montreuil, le 1er janvier 2026"
