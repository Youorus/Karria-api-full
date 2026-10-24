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
| [0010](0010-sessions-opaques-et-connexion-par-code.md) | Sessions opaques à rotation, connexion par code e-mail, compte né de la preuve | Acceptée |
| [0011](0011-verification-des-jetons-apple-et-google.md) | Vérification locale des jetons Apple et Google, rattachement par le `sub` | Acceptée |
| [0012](0012-appareils-et-notifications-push.md) | Appareils déclarés par installation, notifications par Expo | Acceptée |
| [0013](0013-gemini-par-port-et-politique-anti-injection.md) | Gemini derrière des ports, par l'API REST, sortie à schéma imposé et réponse traitée comme une donnée | Acceptée |
| [0014](0014-persistance-du-cv-et-du-brouillon.md) | CV et brouillon en documents JSONB, un par personne, photo retenue par identifiant | Acceptée |
| [0015](0015-suppression-de-compte-et-retention.md) | Suppression de compte : effacement physique, trace de départ anonyme, oubli par événement | Acceptée |
| [0016](0016-moteur-de-recherche-d-offres.md) | Moteur d'offres : collecte découplée, base de vérité, index hybride, porte d'intitulé | Proposée |
| [0017](0017-france-travail-et-planificateur-de-collecte.md) | France Travail en couche de base, collecte continue, planificateur piloté par la demande | Proposée |
| [0018](0018-couche-llm-et-agent-karria.md) | Une couche LLM indépendante du fournisseur, et Karria : constitution, mémoire, garde-fous | Proposée |
| [0019](0019-apprentissage-continu.md) | Apprentissage continu : journal des gestes avec leur place, classement appris, porte de promotion, dérive | Proposée |
| [0020](0020-modeles-de-cv-et-exports.md) | Modèles de CV : spécification canonique tirée du PDF, deux rendus (PDF, DOCX), score ATS déterministe | Proposée |
