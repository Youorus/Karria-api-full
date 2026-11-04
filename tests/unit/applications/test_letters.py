"""La lettre : sa forme imposée, ses bornes, ses retouches."""

from __future__ import annotations

import pytest

from doubles import sample_letter_json
from karria.modules.applications.domain import (
    Letter,
    LetterInvalidError,
    ParagraphKind,
    validated_paragraph,
)

pytestmark = pytest.mark.unit


def test_une_lettre_valide_fait_l_aller_retour_json() -> None:
    letter = Letter.validated(sample_letter_json())

    assert [p.kind for p in letter.paragraphs] == [
        ParagraphKind.INTERET_ENTREPRISE,
        ParagraphKind.INTERET_POSTE,
        ParagraphKind.COMPETENCES,
        ParagraphKind.POURQUOI_MOI,
    ]
    assert Letter.from_json(letter.as_json()) == letter
    assert "Objet : Candidature" in letter.as_text()


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"paragraphs": []}, "paragraphs"),
        ({"paragraphs": [{"kind": "poeme", "text": "x"}]}, "paragraphs.0.kind"),
        ({"paragraphs": [{"kind": "libre", "text": "x" * 1201}]}, "paragraphs.0.text"),
        ({"subject": ""}, "subject"),
        ({"closing": "   "}, "closing"),
    ],
)
def test_une_lettre_hors_forme_est_refusee_avec_le_champ(
    patch: dict[str, object], field: str
) -> None:
    with pytest.raises(LetterInvalidError) as excinfo:
        Letter.validated({**sample_letter_json(), **patch})

    assert excinfo.value.details["reason"] == "letter_invalid"
    assert excinfo.value.details["fields"][0]["field"] == field


def test_un_paragraphe_se_remplace_en_gardant_sa_sorte() -> None:
    letter = Letter.validated(sample_letter_json())

    updated = letter.with_paragraph(2, "Nouveau texte.")

    assert updated.paragraphs[2].text == "Nouveau texte."
    assert updated.paragraphs[2].kind is ParagraphKind.COMPETENCES
    assert letter.paragraphs[2].text != "Nouveau texte."


def test_un_paragraphe_vide_ou_trop_long_est_refuse() -> None:
    assert validated_paragraph("  Bonjour  ") == "Bonjour"
    with pytest.raises(LetterInvalidError):
        validated_paragraph("   ")
    with pytest.raises(LetterInvalidError):
        validated_paragraph("x" * 1201)
