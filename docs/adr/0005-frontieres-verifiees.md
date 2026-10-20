# ADR-0005 — Frontières d'architecture vérifiées mécaniquement

**Statut :** Acceptée — 2026-10-20, reprise du cadre `zoumani_api`

## Contexte

C'est la pièce dont l'absence a coûté le plus cher à la V1. Les intentions
d'architecture y étaient réelles, mais rien ne les vérifiait. Le graphe de
dépendances a dérivé sans que personne ne s'en aperçoive : quatre-vingt-dix
imports vers `app.ai`, soixante-sept vers `app.users`, des modules
atteignant les entrailles les uns des autres.

Aucune de ces lignes n'a été écrite de mauvaise foi. Chacune était le
chemin le plus court, un jour où il fallait livrer.

## Décision

Les quatre règles d'architecture sont encodées en contrats `import-linter`
dans `pyproject.toml` et vérifiées **à chaque commit** (pre-commit) et
**à chaque PR** (CI). Un import illégal casse le build.

| Règle | Contrat |
|-------|---------|
| 1 | `shared/` ignore `modules/` et `bootstrap/` |
| 2 | Dans un module : `api → infrastructure → application → domain` |
| 2 bis | `domain/` n'importe ni FastAPI, ni Starlette, ni Tortoise, ni la configuration, ni la base, ni HTTP, ni la journalisation |
| 4 | Les modules ne s'importent pas entre eux — ils communiquent par le bus d'événements |

## Justification

- **Une règle écrite dans un README est une intention. Une règle vérifiée
  en CI est une garantie.** La différence n'est pas rhétorique : c'est la
  différence entre une architecture qui tient trois ans et une qui tient
  trois mois.
- **Le coût de correction est minimal au moment de l'écriture.** Un import
  illégal détecté le jour même se corrige en cinq minutes. Découvert
  dix-huit mois plus tard, avec deux cents lignes bâties dessus, il se
  négocie en trimestres.
- **La règle 2 bis a une conséquence concrète et vérifiable** : les tests
  de domaine s'exécutent sans base de données. Si un test unitaire réclame
  PostgreSQL, c'est qu'une frontière a été franchie quelque part.

## Conséquences

- Une frontière contournée est visible immédiatement, par tout le monde.
- Une exception reste possible, mais elle devient **explicite** : il faut
  la déclarer en `ignore_imports` et la justifier par une ADR. Le coût du
  contournement passe de zéro à « une décision documentée ».
- Le registre des contrats grandit avec les modules : la règle 2 liste les
  conteneurs, à tenir à jour lors de l'ajout d'un module.

## Alternative écartée

**Revue de code humaine.** Elle attrape la première violation, parfois la
deuxième, jamais la centième. La V1 en est la démonstration.
