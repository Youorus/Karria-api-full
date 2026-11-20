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

D'où vient le compte, sans le demander
--------------------------------------
Le compte retient, **une fois**, le réglage « Région » du téléphone (code
ISO 3166-1 alpha-2) et son fuseau horaire, tels que l'app les annonce à
l'inscription (ADR-0042). C'est une statistique d'origine, pas une
adresse : elle ne décide d'aucun droit, ne se corrige pas, et n'est jamais
déduite d'une adresse IP. Une valeur hors forme est **ignorée**, jamais
refusée — elle ne doit pas coûter une connexion.
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

#: Un code de région : deux lettres majuscules, rien d'autre. `419`
#: (« Amérique latine », que certaines langues portent) n'est pas un pays.
SIGNUP_REGION_LENGTH: Final = 2
_REGION_PATTERN: Final = re.compile(r"[A-Z]{2}")
#: `ZZ` est le « région inconnue » d'Unicode : le garder compterait un pays.
_UNKNOWN_REGION: Final = "ZZ"

#: Un nom de fuseau IANA : `Europe/Brussels`, `America/Argentina/Buenos_Aires`,
#: `UTC`, `Etc/GMT+1`. Le plus long nom publié fait 32 caractères.
SIGNUP_TIMEZONE_MAX_LENGTH: Final = 64
_TIMEZONE_PATTERN: Final = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}")


def parse_signup_region(raw: object) -> str | None:
    """Le code de région s'il a exactement la forme attendue, sinon `None`.

    Strict et sans réparation : ni mise en majuscules, ni blancs ôtés
    (`fullmatch` : un saut de ligne final ne passe pas non plus). L'app
    envoie la forme exacte ; tout le reste est une valeur qu'on ne comprend
    pas, et une statistique fausse vaut moins qu'une statistique absente.
    """
    if not isinstance(raw, str) or not _REGION_PATTERN.fullmatch(raw) or raw == _UNKNOWN_REGION:
        return None
    return raw


def parse_signup_timezone(raw: object) -> str | None:
    """Le nom de fuseau s'il est borné et de forme IANA, sinon `None`."""
    if (
        not isinstance(raw, str)
        or len(raw) > SIGNUP_TIMEZONE_MAX_LENGTH
        or not _TIMEZONE_PATTERN.fullmatch(raw)
    ):
        return None
    return raw


@dataclass(frozen=True, slots=True)
class SignupOrigin:
    """D'où vient un compte, d'après l'appareil : région et fuseau, ou rien.

    Les deux valeurs sont indépendantes — l'une peut manquer sans l'autre —
    et chacune ne s'écrit qu'**une fois** : `completed_by` ne remplace
    jamais une valeur déjà connue.
    """

    region: str | None = None
    timezone: str | None = None

    @classmethod
    def parse(cls, *, region: object, timezone: object) -> SignupOrigin:
        """Lit ce que l'app annonce ; ce qui sort du cadre devient `None`."""
        return cls(region=parse_signup_region(region), timezone=parse_signup_timezone(timezone))

    @property
    def is_empty(self) -> bool:
        """Rien n'est connu."""
        return self.region is None and self.timezone is None

    @property
    def is_complete(self) -> bool:
        """Tout est connu : plus rien ne s'y ajoutera."""
        return self.region is not None and self.timezone is not None

    def completed_by(self, other: SignupOrigin) -> SignupOrigin:
        """Complète ce qui manque avec `other`, sans jamais écraser."""
        return SignupOrigin(
            region=self.region if self.region is not None else other.region,
            timezone=self.timezone if self.timezone is not None else other.timezone,
        )


#: L'origine d'un compte dont on ne sait rien.
UNKNOWN_ORIGIN: Final = SignupOrigin()


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
        signup_origin: Région et fuseau du téléphone à l'inscription, écrits
            une seule fois (ADR-0042). Statistique, jamais une règle.
    """

    id: UUID
    email: EmailAddress
    email_verified_at: datetime
    consent: Consent
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None
    signup_origin: SignupOrigin = UNKNOWN_ORIGIN

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

    def with_signup_origin(self, origin: SignupOrigin) -> User:
        """Complète l'origine du compte, sans jamais écraser ce qui est connu."""
        completed = self.signup_origin.completed_by(origin)
        if completed == self.signup_origin:
            return self
        return replace(self, signup_origin=completed)


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
