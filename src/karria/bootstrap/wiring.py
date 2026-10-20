"""Branchements entre modules.

Le problème que ce fichier résout
---------------------------------
Un module ne peut pas en importer un autre (règle 4) : le fil d'offres
n'a pas le droit de connaître les comptes. Le bus d'événements règle la
plupart des échanges, mais pas ceux qui sont **synchrones et bloquants** —
« ce compte a-t-il encore du quota ? » conditionne l'acceptation de la
requête en cours et ne peut pas attendre un message.

Pour ces cas, le module demandeur déclare un **port** exprimant son besoin
sans nommer qui y répond, et `bootstrap/` — seule couche autorisée à voir
plusieurs modules — substitue l'implémentation réelle.

Pourquoi `dependency_overrides` et non un import direct
--------------------------------------------------------
C'est le mécanisme que FastAPI prévoit déjà, celui-là même que les tests
utilisent. Le réutiliser évite d'inventer un conteneur d'injection maison,
et rend le branchement visible en un seul endroit plutôt que dispersé dans
des `import` que le contrat d'architecture refuserait.

Ce fichier grandira à chaque nouveau branchement. C'est voulu : sa
longueur est la mesure honnête du couplage synchrone entre modules, et une
raison de préférer le bus quand c'est possible. Il est vide aujourd'hui
parce qu'un seul module existe — et c'est exactement ce qu'il doit dire.

La règle de chaque branchement
------------------------------
Le port garde une implémentation par défaut qui **refuse** : un
branchement oublié produit un 403 bruyant plutôt qu'une faille silencieuse.
Chaque ligne ajoutée ici porte, en commentaire, ce qui casserait sans elle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from karria.shared.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)


def wire_modules(app: FastAPI) -> None:
    """Branche les ports inter-modules sur leurs implémentations réelles.

    Appelé par `create_app`. Sans cet appel, les ports concernés gardent
    leur implémentation par défaut — qui **refuse**.
    """
    _ = app
    logger.debug("bootstrap.modules_wired", bindings=0)
