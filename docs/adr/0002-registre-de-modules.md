# ADR-0002 — Registre de modules explicite

**Statut :** Acceptée — 2026-10-20, reprise du cadre `zoumani_api`

## Contexte

Le `main.py` de la V1 contenait cinquante lignes d'imports et
quarante-et-un appels à `include_router`. Chaque nouvelle fonctionnalité
imposait de modifier le point d'entrée de l'application — une violation du
principe ouvert/fermé, et une source permanente de conflits de fusion.

Pire : la liste des modèles transmise à l'ORM était tenue **ailleurs**.
Rien ne garantissait qu'un module enregistré ait ses tables migrées.

## Décision

Chaque module expose un `ModuleDescriptor` depuis son `__init__.py`. Un
registre unique — `bootstrap/modules.py` — liste ces descripteurs et
alimente **toutes** les préoccupations qui dépendent de la liste des
modules : routes HTTP, modèles pour Aerich, abonnements aux événements.

Ajouter un module coûte **une ligne**, à **un seul endroit**.

## Justification

- **`main.py` devient invariant.** Il ne connaît aucun module et n'a plus
  de raison de changer.
- **Une seule source de vérité.** Les tables et les routes étant dérivées
  du même registre, il est structurellement impossible d'enregistrer un
  module en oubliant ses migrations.
- **Composition explicite.** Lire un fichier suffit à connaître la surface
  complète de l'application.

## Conséquences

- Un module se déclare lui-même ; l'assemblage ne connaît pas sa structure
  interne.
- Désactiver un module en production revient à commenter une ligne.
- Un test peut construire une application avec un sous-ensemble de modules.

## Alternative écartée

**Découverte automatique** (`pkgutil.walk_packages` sur `modules/`).
Supprimerait aussi cette ligne, mais au prix d'un chargement implicite :
aucun outil statique ne saurait plus quels modules composent
l'application, l'ordre d'import deviendrait imprévisible, et un dossier
laissé par erreur dans `modules/` serait exposé en production. Une ligne
visible vaut mieux qu'une magie invisible.
