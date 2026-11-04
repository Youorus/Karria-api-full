"""La lettre de motivation d'un dossier : sa forme, ses bornes, sa relecture.

Contrat
-------
Une lettre tient sur une page : un destinataire, un objet, trois à cinq
paragraphes typés, une formule de politesse. Chaque paragraphe porte une
**sorte** (`kind`) — ce que l'app affiche comme titre de section et ce que
le module `documents` sait mettre en page :

| `kind` | Ce que dit le paragraphe |
|---|---|
| `interet_entreprise` | pourquoi cette entreprise |
| `interet_poste` | pourquoi ce poste |
| `competences` | ce que la personne apporte, en miroir des exigences |
| `pourquoi_moi` | ce qui la distingue, et la disponibilité |
| `libre` | un paragraphe écrit par la personne, sans sorte imposée |

La sortie du modèle (`LetterOutput`) et la lettre envoyée par l'app passent
par le même schéma (`LetterShape`) : une lettre hors bornes n'existe pas,
d'où qu'elle vienne (AGENTS.md §6.14).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from karria.modules.applications.domain.errors import LetterInvalidError


class ParagraphKind(StrEnum):
    """La sorte d'un paragraphe."""

    INTERET_ENTREPRISE = "interet_entreprise"
    INTERET_POSTE = "interet_poste"
    COMPETENCES = "competences"
    POURQUOI_MOI = "pourquoi_moi"
    LIBRE = "libre"


#: L'ordre d'une lettre ébauchée par Karria.
DRAFT_KINDS: Final[tuple[ParagraphKind, ...]] = (
    ParagraphKind.INTERET_ENTREPRISE,
    ParagraphKind.INTERET_POSTE,
    ParagraphKind.COMPETENCES,
    ParagraphKind.POURQUOI_MOI,
)
MIN_PARAGRAPHS: Final = 1
MAX_PARAGRAPHS: Final = 6
PARAGRAPH_MAX_LENGTH: Final = 1200
#: Une consigne courte de la personne pour réécrire un paragraphe.
INSTRUCTION_MAX_LENGTH: Final = 300

ShortLine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ParagraphText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PARAGRAPH_MAX_LENGTH)
]


class ParagraphShape(BaseModel):
    """Un paragraphe, tel qu'il est stocké, servi et imposé au modèle."""

    model_config = ConfigDict(extra="ignore")

    kind: ParagraphKind
    text: ParagraphText


class LetterShape(BaseModel):
    """La lettre entière. Même schéma pour la sortie du modèle et l'édition manuelle."""

    model_config = ConfigDict(extra="ignore")

    recipient: ShortLine = Field(description="« Madame, Monsieur, » ou le service nommé.")
    subject: ShortLine = Field(description="L'objet : « Candidature au poste de … »")
    paragraphs: list[ParagraphShape] = Field(min_length=MIN_PARAGRAPHS, max_length=MAX_PARAGRAPHS)
    closing: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ] = Field(description="La formule de politesse, sans la signature.")


class ParagraphOutput(BaseModel):
    """Ce que le modèle rend quand on lui redemande un seul paragraphe."""

    model_config = ConfigDict(extra="ignore")

    text: ParagraphText


@dataclass(frozen=True, slots=True)
class Paragraph:
    """Un paragraphe de la lettre."""

    kind: ParagraphKind
    text: str


@dataclass(frozen=True, slots=True)
class Letter:
    """La lettre d'un dossier."""

    recipient: str
    subject: str
    paragraphs: tuple[Paragraph, ...]
    closing: str

    @classmethod
    def validated(cls, data: Mapping[str, Any]) -> Letter:
        """Une lettre depuis un document JSON (modèle ou app), revalidée.

        Raises:
            LetterInvalidError: La forme ne tient pas.
        """
        try:
            shape = LetterShape.model_validate(data)
        except ValidationError as exc:
            raise LetterInvalidError(
                [
                    {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                    for e in exc.errors()
                ]
            ) from exc
        return cls(
            recipient=shape.recipient,
            subject=shape.subject,
            paragraphs=tuple(Paragraph(p.kind, p.text) for p in shape.paragraphs),
            closing=shape.closing,
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Letter:
        """Relit la forme stockée, sans la rejuger (elle l'a été à l'écriture)."""
        return cls(
            recipient=str(data["recipient"]),
            subject=str(data["subject"]),
            paragraphs=tuple(
                Paragraph(ParagraphKind(p["kind"]), str(p["text"])) for p in data["paragraphs"]
            ),
            closing=str(data["closing"]),
        )

    def as_json(self) -> dict[str, Any]:
        """La forme stockée et servie."""
        return {
            "recipient": self.recipient,
            "subject": self.subject,
            "paragraphs": [{"kind": p.kind.value, "text": p.text} for p in self.paragraphs],
            "closing": self.closing,
        }

    def with_paragraph(self, index: int, text: str) -> Letter:
        """La même lettre, un paragraphe remplacé (sa sorte conservée)."""
        paragraphs = list(self.paragraphs)
        paragraphs[index] = Paragraph(paragraphs[index].kind, text)
        return Letter(self.recipient, self.subject, tuple(paragraphs), self.closing)

    def as_text(self) -> str:
        """La lettre lisible d'un bloc — ce qu'on montre au modèle pour une réécriture."""
        body = "\n\n".join(p.text for p in self.paragraphs)
        return f"{self.recipient}\n\nObjet : {self.subject}\n\n{body}\n\n{self.closing}"


def validated_paragraph(text: str) -> str:
    """Un texte de paragraphe revalidé (bornes, blancs).

    Raises:
        LetterInvalidError: Vide ou trop long.
    """
    try:
        return ParagraphOutput.model_validate({"text": text}).text
    except ValidationError as exc:
        raise LetterInvalidError(
            [{"field": "text", "message": e["msg"]} for e in exc.errors()]
        ) from exc


def kinds_of(paragraphs: Sequence[Paragraph]) -> tuple[str, ...]:
    """Les sortes, dans l'ordre — pour les journaux."""
    return tuple(p.kind.value for p in paragraphs)
