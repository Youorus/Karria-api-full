"""La forme des données d'un CV — celle de la V1, champ pour champ.

Contrat
-------
Ces modèles sont **la définition du domaine** : ce qu'une expérience, une
formation, une compétence *sont*, avec leurs invariants (une date de fin
après la date de début, un niveau parmi l'énumération, une puce de
mission non vide). Trois frontières les emploient, et c'est voulu :

- la route `PUT /cv/draft/chapters/{chapter}` **revalide** ce que le client
  envoie — il n'est jamais cru sur parole ;
- le cas d'usage d'interprétation **valide la sortie du modèle** avant de
  la rendre (AGENTS.md §6.14) ;
- la persistance les relit à chaque chargement, de sorte qu'une ligne
  corrompue en base se voit au lieu de se propager.

Pourquoi Pydantic dans `domain/`
--------------------------------
La règle du domaine veut la bibliothèque standard seule. Pydantic est une
bibliothèque de **validation de données** sans I/O, pas un framework : il
n'impose ni serveur, ni base, ni réseau, et ces modèles se testent en une
milliseconde. Réécrire la validation à la main — une centaine de
contraintes — produirait exactement la duplication que la règle cherche
à éviter. Le choix est consigné dans l'ADR-0013 et vérifié : `domain/`
n'importe toujours ni FastAPI, ni Tortoise, ni le socle.

Noms de champs
--------------
Ceux de la V1 (`app/cv/schemas.py`) et du contrat : `position`, `company`,
`is_current`, `is_in_progress`, `missions`… Renommer l'un d'eux casserait
l'application. Chaque élément de liste porte un `id` stable : la génération
de CV (V1 `source_reconciler`) rattache ses sorties aux éléments d'origine
par identifiant.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Final
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from karria.modules.cv.domain.chapters import Chapter
from karria.modules.cv.domain.normalization import (
    LanguageLevel,
    SkillLevel,
    clean_bullets,
    coerce_language_level,
    coerce_skill_level,
    normalize_year_month,
)

_EMAIL: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Un schéma d'URL quelconque — `https:`, mais aussi `javascript:` ou
#: `data:` : s'il est là, on le juge ; s'il manque, on suppose `https`.
_URL_SCHEME: Final = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def _blank_to_none(value: object) -> object:
    """Un texte vide ou fait de blancs vaut « non renseigné »."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not _EMAIL.match(email):
        msg = "Adresse e-mail invalide."
        raise ValueError(msg)
    return email


def _normalize_url(value: str) -> str:
    """Complète le schéma manquant (« linkedin.com/in/x ») et refuse le reste."""
    url = value.strip()
    if not _URL_SCHEME.match(url):
        url = f"https://{url}"
    if not url.lower().startswith(("http://", "https://")):
        msg = "Seules les adresses http(s) sont acceptées."
        raise ValueError(msg)
    return url


def _normalize_missions(value: object) -> object:
    if isinstance(value, str):
        return clean_bullets(value.splitlines())
    if isinstance(value, list):
        return clean_bullets([str(item) for item in value])
    return value


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortText = Annotated[Text, StringConstraints(max_length=150)]
OptionalShortText = Annotated[
    Annotated[str, StringConstraints(strip_whitespace=True, max_length=150)] | None,
    BeforeValidator(_blank_to_none),
]
OptionalLongText = Annotated[
    Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None,
    BeforeValidator(_blank_to_none),
]
YearMonth = Annotated[
    str | None,
    BeforeValidator(_blank_to_none),
    AfterValidator(normalize_year_month),
    Field(
        description="Format AAAA-MM (2021-09), ou AAAA seul si le mois est inconnu. null si inconnu."
    ),
]
Email = Annotated[str, AfterValidator(_normalize_email)]
Url = Annotated[Annotated[str, StringConstraints(max_length=500)], AfterValidator(_normalize_url)]
Missions = Annotated[
    list[Annotated[str, StringConstraints(max_length=300)]],
    BeforeValidator(_normalize_missions),
    Field(max_length=15),
]


class CvModel(BaseModel):
    """Base commune : espaces réduits, champs inconnus ignorés.

    `extra="ignore"` plutôt que `forbid` : l'application peut envoyer un
    champ facultatif de la V1 (`description`) que le chapitre n'affiche
    pas, et un modèle de langage peut en ajouter un. Ni l'un ni l'autre
    n'est une faute — ce qui compte, c'est que ce qu'on **garde** soit
    valide.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


class ItemModel(CvModel):
    """Un élément de liste, identifié de façon stable."""

    id: UUID = Field(default_factory=uuid4)


class Link(CvModel):
    """Un lien du contact : LinkedIn, portfolio, GitHub…"""

    label: Annotated[Text, StringConstraints(max_length=60)]
    url: Url


class Identity(CvModel):
    """Qui est la personne, et ce qu'elle vise."""

    first_name: Annotated[Text, StringConstraints(max_length=100)]
    last_name: Annotated[Text, StringConstraints(max_length=100)]
    headline: OptionalShortText = None


class Contact(CvModel):
    """Comment la joindre. Au moins un canal est exigé."""

    email: Annotated[Email | None, BeforeValidator(_blank_to_none)] = None
    phone: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None,
        BeforeValidator(_blank_to_none),
    ] = None
    city: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None,
        BeforeValidator(_blank_to_none),
    ] = None
    links: list[Link] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _at_least_one_channel(self) -> Contact:
        if self.email is None and self.phone is None:
            msg = "Un e-mail ou un téléphone est nécessaire pour être recontacté."
            raise ValueError(msg)
        return self


class Summary(CvModel):
    """Le « à propos », à la première personne."""

    summary: Annotated[Text, StringConstraints(max_length=1500)]


class Experience(ItemModel):
    """Une expérience professionnelle, au format V1."""

    position: ShortText
    company: ShortText
    location: OptionalShortText = None
    start_date: YearMonth = None
    end_date: YearMonth = None
    is_current: bool = False
    missions: Missions = Field(default_factory=list)
    description: OptionalLongText = None

    @model_validator(mode="after")
    def _coherent_period(self) -> Experience:
        if self.is_current:
            self.end_date = None
        elif self.start_date and self.end_date and self.end_date < self.start_date:
            msg = "La date de fin précède la date de début."
            raise ValueError(msg)
        return self


class Education(ItemModel):
    """Une formation, au format V1."""

    degree: ShortText
    school: ShortText
    location: OptionalShortText = None
    start_date: YearMonth = None
    end_date: YearMonth = None
    is_in_progress: bool = False
    description: OptionalLongText = None

    @model_validator(mode="after")
    def _coherent_period(self) -> Education:
        if self.is_in_progress:
            self.end_date = None
        elif self.start_date and self.end_date and self.end_date < self.start_date:
            msg = "La date de fin précède la date de début."
            raise ValueError(msg)
        return self


class Skill(ItemModel):
    """Une compétence et son niveau."""

    name: Annotated[Text, StringConstraints(max_length=100)]
    level: Annotated[SkillLevel, BeforeValidator(coerce_skill_level)]
    category: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None,
        BeforeValidator(_blank_to_none),
    ] = None


class Language(ItemModel):
    """Une langue et son niveau."""

    language: Annotated[Text, StringConstraints(max_length=100)]
    level: Annotated[LanguageLevel, BeforeValidator(coerce_language_level)]
    certification: OptionalShortText = None


class SkillsChapter(CvModel):
    """Le chapitre « compétences » : compétences et langues ensemble."""

    skills: list[Skill] = Field(default_factory=list, max_length=40)
    languages: list[Language] = Field(default_factory=list, max_length=15)


class Interest(ItemModel):
    """Un centre d'intérêt."""

    name: Annotated[Text, StringConstraints(max_length=100)]
    category: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None,
        BeforeValidator(_blank_to_none),
    ] = None
    description: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None,
        BeforeValidator(_blank_to_none),
    ] = None


class InterestsChapter(CvModel):
    """Le chapitre « passions »."""

    interests: list[Interest] = Field(default_factory=list, max_length=20)


class PhotoChapter(CvModel):
    """Le choix de photo : l'identifiant d'une photo envoyée, ou aucune.

    Un identifiant et non une URL : l'URL rendue par `POST /cv/photo` est
    signée et expire, et on ne stocke jamais d'URL (ADR-0008). Le client
    nous rend ce qu'on lui a donné, et on retrouve la clé chez nous.
    """

    image_id: UUID | None = None


class Project(ItemModel):
    """Un projet, au format V1. Aucun chapitre ne l'alimente aujourd'hui.

    Conservé pour que la génération de CV retrouve la structure complète
    de la V1 ; un import ou une édition manuelle pourront le remplir.
    """

    title: Annotated[Text, StringConstraints(max_length=200)]
    description: OptionalLongText = None
    context: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None,
        BeforeValidator(_blank_to_none),
    ] = None
    technologies: list[Annotated[str, StringConstraints(max_length=60)]] = Field(
        default_factory=list, max_length=30
    )
    link: Annotated[Url | None, BeforeValidator(_blank_to_none)] = None
    github: Annotated[Url | None, BeforeValidator(_blank_to_none)] = None


#: Le schéma que chaque chapitre produit. Pour un chapitre en boucle, c'est
#: le schéma d'**un** élément.
CHAPTER_SCHEMAS: Final[dict[Chapter, type[CvModel]]] = {
    Chapter.IDENTITY: Identity,
    Chapter.CONTACT: Contact,
    Chapter.SUMMARY: Summary,
    Chapter.EXPERIENCES: Experience,
    Chapter.EDUCATIONS: Education,
    Chapter.SKILLS: SkillsChapter,
    Chapter.INTERESTS: InterestsChapter,
    Chapter.PHOTO: PhotoChapter,
}


def validate_chapter_payload(chapter: Chapter, payload: Any) -> dict[str, Any]:
    """Valide une valeur contre le schéma de son chapitre et la rend normalisée.

    Rend un dictionnaire sérialisable (`mode="json"`) : c'est la forme
    stockée dans le brouillon et renvoyée au client, identique dans les
    deux sens.

    Raises:
        pydantic.ValidationError: La valeur ne respecte pas le schéma.
    """
    return CHAPTER_SCHEMAS[chapter].model_validate(payload).model_dump(mode="json")
