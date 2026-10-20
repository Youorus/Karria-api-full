"""Couche d'assemblage de l'application.

Seule couche autorisée à connaître **à la fois** `shared/` et `modules/`.
C'est elle qui câble le tout ; personne ne câble à sa place.
"""

from karria.bootstrap.app import create_app

__all__ = ["create_app"]
