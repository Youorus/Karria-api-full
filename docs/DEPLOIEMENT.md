# Mettre Karria en production

> **Statut : à écrire au premier déploiement.** La cible d'hébergement de
> la refonte n'est pas décidée (voir `PLUS-TARD.md`). Ce document décrira
> **la séquence** ; les valeurs vivront dans le fichier de composition de
> production, jamais ici — les répéter garantirait qu'un jour les deux se
> contredisent.

## Ce qui est déjà vrai, quel que soit l'hébergeur

- L'image se construit par le `Dockerfile` (deux étapes, utilisateur non
  privilégié, Gunicorn supervisant des workers Uvicorn). La CI vérifie
  qu'elle construit à chaque PR.
- La configuration passe par variables d'environnement (`.env.example`
  liste chaque bloc). En environnement déployé, `Settings` **refuse de
  démarrer** si un secret est resté par défaut, si un joker réseau traîne,
  si le stockage n'est pas durable ou si les e-mails sortent en console.
- Deux secrets se génèrent et ne doivent **jamais** être identiques :
  `SECURITY__SECRET_KEY` et `SECURITY__TOKEN_PEPPER`
  (`python -c "import secrets; print(secrets.token_urlsafe(64))"`).
- `GEMINI__API_KEY` (module `cv`) : sans elle, la création du CV, la
  transcription et l'import répondent 503 `ai_unavailable` ; le démarrage
  l'annonce (`config.gemini_disabled`) sans bloquer.
- Les buckets se créent par `make storage-provision`, idempotent, à relancer
  après chaque déploiement. Le nom physique porte l'environnement
  (`karria-production-media`) : la recette ne peut pas écraser la production.
- Les migrations s'appliquent par `aerich upgrade`, **jamais** au démarrage
  du conteneur (AGENTS.md §6.4).
- `make doctor` avec la configuration de production dit si l'environnement
  est prêt, **avant** de couper le service.
- Deux blocs de configuration arrivent avec les premiers modules métier :
  `SOCIAL__*` (audiences Apple et Google — sans Google, l'API démarre et
  avertit) et `PUSH__*` (service push d'Expo). Voir `.env.example`.
- Une tâche planifiée lit les reçus de notifications toutes les quinze
  minutes : `uv run python -m scripts.push_receipts` (ADR-0012).
- `SECURITY__TOKEN_PEPPER` poivre les condensats de jetons et de codes : le
  changer déconnecte **tous** les utilisateurs.
