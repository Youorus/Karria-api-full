# Décisions d'architecture (ADR)

Une ADR consigne une décision structurante, son **contexte** et ses
**conséquences**. Elle répond à la question que se posera un développeur
dans deux ans : *pourquoi est-ce fait comme ça ?*

C'est le manque principal de la V1 : les décisions y étaient lisibles dans
le code, mais leurs raisons avaient disparu. Résultat, on n'osait plus
toucher à ce qu'on ne comprenait plus, et on empilait à côté.

## Règles

- **Une ADR est immuable.** On ne la corrige pas : on en écrit une nouvelle
  qui la remplace, en indiquant `Remplace : ADR-000X`.
- **Une décision reversée garde son ADR** et en reçoit une nouvelle qui
  la corrige, avec un en-tête `Revient sur :`. Effacer la première
  effacerait la raison pour laquelle on avait tranché ainsi — et l'on
  referait le même détour six mois plus tard.
- **L'index se complète dans le commit qui ajoute l'ADR.** Un index en
  retard fait croire qu'une décision n'existe pas.
- **On écrit une ADR avant d'implémenter**, pas après.
- Statuts : `Proposée`, `Acceptée`, `Remplacée par ADR-000X`.

## Provenance

Les neuf premières ADR sont **reprises du cadre `zoumani_api`**, où elles
ont été prises et éprouvées sur un an. Elles sont acceptées pour Karria le
2026-10-20, telles quelles pour le fond, adaptées dans leurs exemples. Les
décisions **métier** de ce cadre — paiement, voyages, sessions en BFF — ne
sont pas reprises : Karria prendra les siennes, module par module.

## Index

| N° | Titre | Statut |
|----|-------|--------|
| [0001](0001-monolithe-modulaire.md) | Monolithe modulaire plutôt que microservices | Acceptée |
| [0002](0002-registre-de-modules.md) | Registre de modules explicite | Acceptée |
| [0003](0003-tortoise-orm.md) | Tortoise ORM et Aerich | Acceptée, avec réserve |
| [0004](0004-couches-et-ports.md) | Découpage en couches avec ports | Acceptée |
| [0005](0005-frontieres-verifiees.md) | Frontières d'architecture vérifiées mécaniquement | Acceptée |
| [0006](0006-outillage-uv.md) | uv et `pyproject.toml` unique | Acceptée |
| [0007](0007-authentification-et-autorisation.md) | Principal transverse et permissions déclarées par module | Acceptée |
| [0008](0008-stockage-objet.md) | Stockage objet : un port, un adaptateur S3, un registre de buckets | Acceptée |
| [0009](0009-normalisation-des-images-recues.md) | Toute image reçue est normalisée avant d'être écrite | Acceptée |
