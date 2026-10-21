"""Le compte Karria : une adresse prouvée, et les identités qui y mènent.

Ce qu'est un compte, et ce qu'il n'est pas
------------------------------------------
Un compte est une **adresse e-mail prouvée** — par un code reçu, par Apple
ou par Google — accompagnée du consentement donné en continuant. Rien
d'autre : ni nom, ni téléphone, ni date de naissance (contrat de l'app,
`auth.types.ts`). Ce que Karria a besoin de savoir d'une personne vit dans
son CV, pas dans son compte.

Il n'y a **qu'un seul type d'utilisateur** et aucun champ `role` ou
`is_admin` (ADR-0007). Un compte connecté a accès à tout.

Pourquoi la même adresse donne le même compte
---------------------------------------------
Quelqu'un qui s'inscrit par code puis revient par Google doit retrouver son
CV, pas en recommencer un. Le rattachement passe par l'adresse, **à la
seule condition qu'elle soit attestée** par le fournisseur : une adresse
seulement déclarée suffirait sinon à prendre le compte d'autrui.

Pourquoi le `sub` du fournisseur prime sur l'adresse
----------------------------------------------------
Apple ne transmet l'adresse qu'à la **première** connexion ; ensuite, seul
le `sub` arrive. Et une adresse change de mains — un relais Apple se
désactive, un domaine se perd. Le `sub`, lui, est stable et signé : c'est
par lui qu'on reconnaît une personne qui revient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from karria.modules.identity.domain.errors import InvalidEmailError

#: Longueur maximale d'une adresse, RFC 5321.
EMAIL_MAX_LENGTH: Final = 254

#: Une adresse : quelque chose, un `@`, un domaine avec au moins un point.
#: Volontairement simple — la seule preuve qui compte est le code reçu, et
#: une expression plus savante refuserait des adresses légitimes sans
#: jamais en garantir une seule.
_EMAIL_PATTERN: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class EmailAddress:
    """Une adresse normalisée : sans blancs, en minuscules.

    La normalisation est ce qui rend `Marc@Exemple.fr` et `marc@exemple.fr`
    la même personne. Sans elle, deux comptes naîtraient de la même boîte.
    """

    value: str

    @classmethod
    def parse(cls, raw: str) -> EmailAddress:
        """Normalise et valide une saisie.

        Raises:
            InvalidEmailError: La saisie n'a pas la forme d'une adresse.
        """
        value = raw.strip().lower()
        if not value or len(value) > EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(value):
            raise InvalidEmailError
        return cls(value)

    @property
    def masked(self) -> str:
        """L'adresse telle qu'on la rappelle à l'écran : `m•••@exemple.fr`.

        Assez pour que la personne reconnaisse la sienne ; trop peu pour
        qu'un tiers qui connaît un identifiant de parcours la reconstitue.
        """
        local, _, domain = self.value.partition("@")
        return f"{local[:1]}{'•' * max(len(local) - 1, 1)}@{domain}"


@dataclass(frozen=True, slots=True)
class Consent:
    """Le consentement donné en continuant, avec ses versions et sa date.

    Il n'y a plus d'écran où cocher une case : l'écran d'accueil dit
    « en continuant, tu acceptes », et continuer **est** l'acceptation. La
    preuve tient dans les versions que l'app envoie avec sa première
    requête, horodatées ici.
    """

    terms_version: str
    privacy_policy_version: str
    accepted_at: datetime

    def renewed(self, *, terms_version: str, privacy_policy_version: str, at: datetime) -> Consent:
        """Enregistre une nouvelle acceptation si une version a changé.

        Une acceptation identique n'est **pas** réécrite : sa date
        d'origine est ce qui en fait une preuve.
        """
        if (
            terms_version == self.terms_version
            and privacy_policy_version == self.privacy_policy_version
        ):
            return self
        return Consent(
            terms_version=terms_version,
            privacy_policy_version=privacy_policy_version,
            accepted_at=at,
        )


@dataclass(frozen=True, slots=True)
class User:
    """Un compte : une adresse prouvée et un consentement.

    Attributes:
        email_verified_at: Quand l'adresse a été prouvée. Toujours renseigné
            pour un compte né de ce module — il n'y a pas d'autre façon
            d'en créer un — mais conservé explicitement : c'est **la**
            condition du rattachement d'une identité sociale.
        last_login_at: Dernière ouverture de session. Sert au support et à
            un futur nettoyage des comptes dormants, jamais à une règle.
    """

    id: UUID
    email: EmailAddress
    email_verified_at: datetime
    consent: Consent
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None

    def signed_in(self, *, at: datetime) -> User:
        """Note une connexion."""
        return replace(self, last_login_at=at, updated_at=at)

    def with_consent(
        self, *, terms_version: str, privacy_policy_version: str, at: datetime
    ) -> User:
        """Met à jour le consentement si les versions ont changé."""
        renewed = self.consent.renewed(
            terms_version=terms_version, privacy_policy_version=privacy_policy_version, at=at
        )
        if renewed is self.consent:
            return self
        return replace(self, consent=renewed, updated_at=at)


class SocialProvider(StrEnum):
    """Les fournisseurs d'identité acceptés."""

    APPLE = "apple"
    GOOGLE = "google"


@dataclass(frozen=True, slots=True)
class VerifiedSocialProfile:
    """Ce qu'un fournisseur affirme, **une fois la signature vérifiée**.

    Le mot « verified » n'est pas décoratif : une instance n'existe que si
    la signature, l'émetteur, l'audience, l'expiration et le nonce ont été
    contrôlés. Un code qui en construirait une sans ces contrôles
    respecterait le type et ne prouverait rien.

    Attributes:
        subject: Le `sub` signé. La clé stable de la personne chez ce
            fournisseur.
        email: L'adresse transmise, ou `None` — Apple ne l'envoie qu'à la
            première connexion.
        email_verified: Le fournisseur atteste-t-il l'adresse ? Google le
            dit explicitement et il arrive à faux. Apple ne signe que des
            adresses qu'il a vérifiées, relais compris.
    """

    provider: SocialProvider
    subject: str
    email: str | None
    email_verified: bool


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Le rattachement d'un compte à un `sub` de fournisseur.

    `(provider, subject)` est unique : c'est ce qui garantit qu'une personne
    revenant par Apple retrouve son compte plutôt que d'en créer un second.
    L'adresse n'est conservée qu'à titre indicatif — elle n'identifie pas.
    """

    id: UUID
    user_id: UUID
    provider: SocialProvider
    subject: str
    email: str | None
    created_at: datetime
    last_used_at: datetime
