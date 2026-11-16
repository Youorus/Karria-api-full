"""L'accès de démonstration des équipes de revue d'Apple et de Google (ADR-0039).

Le seul moyen d'entrer sans compte Apple ou Google est un code à usage unique
envoyé par e-mail. Google Play exige pourtant des identifiants **réutilisables,
valables de partout, qui contournent** l'étape du code ; Apple demande un
compte de démonstration (règle 2.1(a)). D'où une exception étroite :

- **une seule adresse**, choisie par l'exploitation (une boîte qui n'a pas
  besoin d'exister : rien n'y est envoyé) ;
- pour elle, le code du parcours est le **code fixe** configuré, et **aucun
  courriel ne part** ;
- tout le reste est inchangé : le parcours expire, le code faux compte comme
  un essai, cinq essais au plus, le frein par origine tient. Toute autre
  adresse reçoit son vrai code.

Le compte de démonstration est un compte ordinaire : il naît à la première
connexion, ne contient que ce que l'évaluateur y met, et renaît vide après
une suppression. Si le code fuit, on obtient l'accès à un compte fictif — et
l'on change le code dans la configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from karria.modules.identity.domain.accounts import EmailAddress
from karria.modules.identity.domain.errors import InvalidEmailError
from karria.modules.identity.domain.login import CODE_DIGITS

#: Codes faux admis pour l'adresse de démonstration, **toutes origines
#: confondues**, par fenêtre d'une heure (et par processus). Le code fixe a six
#: chiffres : sans ce frein global, en multipliant les origines et les
#: parcours, on finirait par le deviner. Saturé, le frein refuse aussi le bon
#: code jusqu'à la fin de la fenêtre — un déni de service possible pendant une
#: revue, assumé : le compte est fictif, mais il ne doit pas se deviner.
REVIEW_MAX_FAILURES_PER_HOUR: Final = 30
#: La clé de ce frein : une seule, pour toutes les origines.
REVIEW_THROTTLE_KEY: Final = "review-access"


@dataclass(frozen=True, slots=True)
class ReviewAccess:
    """L'adresse de démonstration et son code fixe."""

    email: str
    code: str

    @classmethod
    def of(cls, email: str, code: str) -> ReviewAccess | None:
        """L'accès, ou `None` si l'adresse n'est pas renseignée.

        Raises:
            ValueError: Adresse invalide, ou code qui n'a pas la forme d'un
                code de connexion (`CODE_DIGITS` chiffres) — une erreur de
                configuration, à refuser au démarrage plutôt qu'à l'usage.
        """
        if not email.strip():
            return None
        try:
            address = EmailAddress.parse(email)
        except InvalidEmailError as exc:
            raise ValueError("l'adresse de démonstration n'est pas une adresse") from exc
        if len(code) != CODE_DIGITS or not code.isdigit():
            raise ValueError(f"le code de démonstration doit faire {CODE_DIGITS} chiffres")
        return cls(email=address.value, code=code)

    def matches(self, email: str) -> bool:
        """Vrai pour l'adresse de démonstration, déjà normalisée."""
        return email == self.email
