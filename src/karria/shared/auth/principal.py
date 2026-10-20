"""L'appelant authentifié, réduit à ce qui est strictement transverse.

Contrat
-------
`Principal` répond à deux questions, et à aucune autre : **qui agit ?** et
**qu'a-t-il le droit de faire ?** Il ne porte ni nom, ni e-mail, ni statut
de compte. Ces informations appartiennent au module qui possède l'entité ;
les recopier ici en ferait des données à synchroniser, donc à désynchroniser.

Une permission est une chaîne `<ressource>:<action>` — `users:suspend`,
`trips:publish`. `shared/` ne connaît aucune de ces valeurs : chaque module
déclare les siennes et les fait respecter sur ses propres routes. C'est la
règle 1 appliquée à l'autorisation : le socle fournit le mécanisme, jamais
la politique.

Pourquoi pas un rôle
--------------------
La V1 portait un booléen `is_admin`. Chaque nouvelle capacité
d'administration élargissait donc le pouvoir de **tous** les
administrateurs, sans qu'aucune ligne de code ne le dise. Une équipe
support qui devait pouvoir lire un compte se retrouvait capable de le
supprimer. Un rôle est une agrégation de permissions : il peut être
reconstitué au-dessus de ce modèle, l'inverse n'est pas vrai.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Principal:
    """Identité et pouvoirs de l'appelant d'une requête.

    Attributes:
        subject_id: Identifiant du sujet authentifié. Opaque pour `shared/` :
            c'est au module concerné de savoir à quoi il correspond.
        permissions: Ensemble figé des permissions accordées. Vide par
            défaut — un appelant authentifié ne peut, par défaut, rien
            faire d'autre que ce qui le concerne lui-même.
    """

    subject_id: UUID
    permissions: frozenset[str] = field(default_factory=frozenset)

    def missing_permissions(self, required: tuple[str, ...]) -> tuple[str, ...]:
        """Retourne les permissions demandées dont l'appelant ne dispose pas.

        L'ordre d'entrée est conservé : le journal de refus est ainsi
        reproductible, ce qu'un `set` ne garantirait pas.
        """
        return tuple(permission for permission in required if permission not in self.permissions)

    def has_permissions(self, *required: str) -> bool:
        """Indique si l'appelant détient **toutes** les permissions demandées."""
        return not self.missing_permissions(required)

    def is_(self, subject_id: UUID) -> bool:
        """Indique si l'appelant est le sujet désigné.

        Sert au contrôle « chacun chez soi » : agir sur sa propre ressource
        ne demande aucune permission, agir sur celle d'autrui en demande une.
        """
        return self.subject_id == subject_id
