"""Le départ d'une personne : ce qu'on retient quand un compte disparaît.

Ce qu'on garde, et pourquoi c'est si peu
----------------------------------------
Quand quelqu'un supprime son compte, Karria efface tout ce qui le concerne
(ADR-0015). Une seule chose survit : **pourquoi** il part, pour que
l'équipe produit apprenne. Cette trace est anonyme par construction : ni
identifiant de compte, ni adresse, ni rien qui permette de remonter à la
personne. Un enregistrement de départ est un fait statistique, pas une
fiche.

Les raisons sont **celles du contrat** (`karria_app`,
`RAISONS_DE_DEPART`) : l'app n'affiche que ces six choix, le serveur
n'accepte que ceux-là. Le texte libre est borné pour la même raison qu'une
réponse de CV : un champ sans plafond est un dépotoir — et il est
**expurgé** de ce qui ressemble à une adresse ou à un numéro de téléphone,
parce qu'une trace anonyme qui garde « écrivez-moi à marc@… » ne l'est plus.

La validation est faite ici et non par Pydantic, comme pour l'adresse à la
connexion : c'est ce qui permet de répondre la raison fine du contrat
(`account_deletion_reason_invalid`…) sous le champ, et non une erreur de
forme générique.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from karria.modules.identity.domain.errors import (
    DepartureDetailsInvalidError,
    DepartureDetailsTooLongError,
    DepartureReasonInvalidError,
)

#: Longueur maximale du mot libre qui accompagne la raison. La valeur du
#: contrat de l'app (`SuppressionCompte.details`).
DEPARTURE_DETAILS_MAX_LENGTH: Final = 1000

#: Ce qui ressemble à une adresse e-mail. Volontairement large : on préfère
#: effacer un faux positif qu'en garder un vrai.
_EMAIL_LIKE: Final = re.compile(r"[^\s@<>()\[\]]+@[^\s@<>()\[\]]+\.[^\s@<>()\[\]]+")
#: Ce qui ressemble à un numéro de téléphone : au moins neuf chiffres, avec
#: ou sans séparateurs usuels, précédés ou non d'un indicatif.
_PHONE_LIKE: Final = re.compile(r"(?<!\w)(?:\+|00)?\d(?:[\s.\-()]*\d){8,}(?!\w)")

EMAIL_PLACEHOLDER: Final = "[e-mail]"
PHONE_PLACEHOLDER: Final = "[téléphone]"


class DepartureReason(StrEnum):
    """Pourquoi une personne supprime son compte — les codes du contrat."""

    FOUND_JOB = "found_job"
    NO_MATCHING_OFFERS = "no_matching_offers"
    NOT_USEFUL = "not_useful"
    TOO_MANY_NOTIFICATIONS = "too_many_notifications"
    PRIVACY = "privacy"
    OTHER = "other"

    @classmethod
    def parse(cls, raw: str) -> DepartureReason:
        """Reconnaît un code du contrat.

        Raises:
            DepartureReasonInvalidError: La valeur n'en est pas un.
        """
        try:
            return cls(raw)
        except ValueError:
            raise DepartureReasonInvalidError from None


def scrub_personal_data(text: str) -> str:
    """Remplace ce qui ressemble à une adresse ou à un numéro par un marqueur.

    La trace de départ doit rester anonyme même si la personne y écrit
    comment la joindre. On ne cherche pas à reconnaître un nom — impossible
    sans faux positifs massifs — mais ce qui est mécaniquement reconnaissable
    et directement exploitable l'est.
    """
    scrubbed = _EMAIL_LIKE.sub(EMAIL_PLACEHOLDER, text)
    return _PHONE_LIKE.sub(PHONE_PLACEHOLDER, scrubbed)


def normalize_details(raw: object) -> str | None:
    """Réduit le mot libre à ce qu'on garde : sans blancs autour, expurgé, `None` si vide.

    Le plafond se mesure sur le texte **tel que la personne l'a écrit**,
    blancs ôtés : l'expurgation peut allonger (un numéro de dix chiffres
    devient `[téléphone]`) et ne doit pas faire refuser un texte qui tenait.

    Raises:
        DepartureDetailsInvalidError: Ce n'est ni un texte ni `None`.
        DepartureDetailsTooLongError: Le texte dépasse le plafond, blancs ôtés.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise DepartureDetailsInvalidError
    details = raw.strip()
    if not details:
        return None
    if len(details) > DEPARTURE_DETAILS_MAX_LENGTH:
        raise DepartureDetailsTooLongError(DEPARTURE_DETAILS_MAX_LENGTH)
    return scrub_personal_data(details)


@dataclass(frozen=True, slots=True)
class DepartureFeedback:
    """Ce que la personne dit en partant : une raison, un mot facultatif."""

    reason: DepartureReason
    details: str | None = None

    @classmethod
    def parse(cls, *, reason: str, details: object) -> DepartureFeedback:
        """Construit un avis normalisé à partir de ce que le client a envoyé.

        Raises:
            DepartureReasonInvalidError: La raison n'est pas un code du contrat.
            DepartureDetailsInvalidError: Le mot libre n'est ni texte ni `None`.
            DepartureDetailsTooLongError: Le mot libre dépasse le plafond.
        """
        return cls(reason=DepartureReason.parse(reason), details=normalize_details(details))


@dataclass(frozen=True, slots=True)
class DepartureRecord:
    """La trace **anonyme** d'un départ, telle qu'on la conserve.

    Attributes:
        account_age_days: Âge du compte à la suppression, en jours entiers.
            Un départ à J+2 et un départ à J+400 ne disent pas la même chose.
        platform: Plateforme de l'appareil principal (`ios`, `android`), si
            un appareil était déclaré. `None` sinon.
        app_version: Version de l'app sur cet appareil, si connue.
        recorded_at: Quand le départ a eu lieu.

    Ce que cette structure ne porte **pas**, et ne portera jamais :
    l'identifiant du compte, l'adresse, l'identifiant d'installation.
    """

    reason: DepartureReason
    details: str | None
    account_age_days: int
    platform: str | None
    app_version: str | None
    recorded_at: datetime


def account_age_in_days(*, created_at: datetime, at: datetime) -> int:
    """L'âge d'un compte en jours entiers, jamais négatif.

    Jamais négatif : une horloge qui recule entre deux serveurs ne doit pas
    produire une statistique absurde.
    """
    return max((at - created_at).days, 0)
