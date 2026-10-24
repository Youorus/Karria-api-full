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
- Anthropic (`LLM__PROVIDERS__ANTHROPIC__API_KEY`) n'entre dans le routage
  qu'avec `LLM__PROVIDERS__ANTHROPIC__WORKSPACE_ID` (en-tête
  `anthropic-workspace-id`, la clé de Karria étant une clé d'utilisateur) ;
  sans lui, `llm.provider_disabled` au démarrage et rien d'autre — sauf si
  une route `LLM__TASKS__*=anthropic:…` est posée, ce qui bloque le démarrage.
- `SECURITY__TOKEN_PEPPER` poivre les condensats de jetons et de codes : le
  changer déconnecte **tous** les utilisateurs.

## Le moteur d'offres : trois processus, une image (ADR-0016)

| Application Dokploy | Commande | Rôle |
|---|---|---|
| `karria-api` | celle du `Dockerfile` (Gunicorn) | les routes, dont `/internal/offers/*` pour le collecteur |
| `karria-worker` | `python -m scripts.worker` | la file durable : ingestion des lots, synchronisation de l'index (chaque minute), alertes, cibles (chaque heure), expiration et réconciliation (chaque nuit) |
| `karria-collector` | `python -m karria.collector` | la collecte continue : France Travail toutes les 3 min + balayage nocturne, JobSpy toutes les 10 min ; aucun secret de l'API, seulement ses identifiants partenaires France Travail |

Même dépôt, même image, `restart: unless-stopped` pour les trois. Le
collecteur n'a **pas** de tâche planifiée Dokploy : sa boucle est son
calendrier (variante : une tâche planifiée `python -m karria.collector
--once` toutes les 10 minutes — jamais les deux).

Mise en service, dans l'ordre :

1. `aerich upgrade` (tables `offers_*`), puis `python -m scripts.queue_schema`
   (tables `procrastinate_*`, une fois ; à relire à chaque montée de
   version de procrastinate) ;
2. `make storage-provision` (bucket `landing`) ;
3. `python -m scripts.offers ensure-indexes` (collections Qdrant
   `karria-<env>-offers` et `-criteria`) ;
4. `python -m scripts.offers refresh-targets` (le socle de 30 cibles) ;
5. démarrer le worker, puis le collecteur ;
6. amorcer France Travail : `python -m karria.collector --ft-sweep 1`
   (la veille, ~24 000 annonces, deux minutes de lecture, dix minutes
   d'ingestion) — ensuite la boucle suffit.

Variables de l'API et du worker : `QDRANT__URL`, `QDRANT__API_KEY`,
`INFERENCE__EMBEDDINGS_URL` (`http://karria-embeddings:80`),
`INFERENCE__EMBEDDING_MODEL` (défaut `intfloat/multilingual-e5-base@int8`,
**ne change qu'avec un ré-indexage** : `python -m scripts.offers reconcile`),
`COLLECTOR__TOKEN` (`openssl rand -hex 32`). Variables du collecteur :
`COLLECTOR__API_URL` (l'API par son nom interne, ex.
`http://karria-api-ujqvaj:8000/api/v1` — ce nom doit figurer dans
`SECURITY__ALLOWED_HOSTS` de l'API, sinon `400 Invalid host header`),
`COLLECTOR__TOKEN` (le même), `COLLECTOR__FRANCE_TRAVAIL_CLIENT_ID` et
`COLLECTOR__FRANCE_TRAVAIL_CLIENT_SECRET` (compte partenaire
francetravail.io ; les URL et la portée ont des défauts corrects),
`COLLECTOR__PROXIES` facultatif. Le fichier d'environnement du collecteur
sur le serveur d'évaluation est `/root/karria-collector.env` (mode 600).

### État réel de la production (2026-10-23)

Serveur `ssh papex-new` (Dokploy v0.30.8, Swarm ; partagé avec Papiers
Express — ne jamais toucher à `papex*`, `/opt/papex` ni au projet Dokploy
« Papiers Express »). Trois applications Dokploy dans le projet Karria,
construites depuis `Youorus/Karria-api-full` (branche `main`). Le webhook
GitHub pointe sur `https://admin.papex.fr/api/deploy/…`, dont le DNS vise
encore l'ancien serveur : **un `push` ne déploie pas**. Déclencher à la main
(`/root/dk.sh application.deploy '{"applicationId": …}'`, une fois par
application, ids dans `/root/karria-dokploy.json`) jusqu'à ce que le DNS
d'`admin.papex.fr` pointe sur 128.140.80.204 :

| Application | Nom de service | Particularités |
|---|---|---|
| API | `karria-api-ujqvaj` | Gunicorn, 2 GiB ; domaines `api.karria.fr` et `api.128-140-80-204.sslip.io` (Let's Encrypt) ; `/health/ready` public |
| worker | `karria-worker-y9xkmo` | `python -m scripts.worker`, 1,5 GiB |
| collecteur | `karria-collector-gwd7ud` | `python -m karria.collector`, 2 GiB, fichier d'env `/root/karria-collector.prod.env` |

Le `HEALTHCHECK` du `Dockerfile` interroge HTTP : sur le worker et le
collecteur il tue le conteneur (`exit 137`, boucle de redémarrage). Ces deux
applications ont donc `healthCheckSwarm = {"Test": ["NONE"]}` dans Dokploy.
Les variables de l'API sont dans `/root/karria-api.env` (mode 600 ; jamais
affiché), qui alimente aussi les conteneurs ponctuels :

```sh
docker run --rm --network dokploy-network --env-file /root/karria-api.env \
  karria-api-ujqvaj:latest aerich upgrade            # ou : python -m scripts.offers reconcile
```

Les appels à l'API Dokploy passent par le cookie `/root/.dokploy-cookie`
(domaine `localhost`, donc `http://localhost:3000/api/...`) et l'aide
`/root/dk.sh <endpoint> <fichier json>` ; les identifiants d'applications
sont dans `/root/karria-dokploy.json`.

Le serveur d'inférence (`karria-embeddings`, composition `karria-donnees`)
sert `/opt/karria/models/e5-base-int8`, produit par
`scripts/quantize_onnx.py` à partir de la révision `d128750` de
`intfloat/multilingual-e5-base`. Il n'y a plus de reclasseur.

## Modèles de CV et exports (ADR-0020, 2026-10-24)

- **Gotenberg 8.38.1** (`karria-gotenberg`) dans la composition Dokploy
  `karria-donnees` : Chromium + LibreOffice, 1 Gio, réseau `dokploy-network`
  seulement (aucun port publié). L'API et le worker le joignent par
  `DOCUMENTS__GOTENBERG_URL=http://karria-gotenberg:3000`.
- **Variables** (API **et** worker — l'ingestion tourne dans le worker) :
  `DOCUMENTS__GOTENBERG_URL`, `DOCUMENTS__ADMIN_TOKEN` (routes
  `/internal/templates`, jeton gardé dans `/root/karria-documents-admin.token`,
  mode 600, jamais affiché), facultatives `LLM__TASKS__TEMPLATE_EXTRACT` et
  `LLM__TASKS__ATS_EXPLAIN` (Gemini par défaut).
- **Bucket** `karria-production-templates` (PDF sources, aperçus, pictogrammes,
  bibliothèque de polices `fonts/…`) : `python -m scripts.storage_provision`.
  Le bucket `documents` accepte désormais le DOCX.
- **Migration** 8 (`modeles_de_cv`) : `aerich upgrade`.
- **Déposer un modèle** :
  `docker run --rm --network dokploy-network --env-file /root/karria-api.env -v /root/templates:/in:ro karria-api-ujqvaj:latest python -m scripts.templates submit /in/x.pdf --name x --label X --inline`
  (ou `POST /api/v1/internal/templates`, analyse dans le worker). Revue :
  `python -m scripts.templates list`.
- **URL signées** : tant que `STORAGE__ENDPOINT_URL` est interne, poser
  `STORAGE__PUBLIC_ENDPOINT_URL=https://storage.karria.fr` après avoir créé le
  domaine (DNS + domaine Dokploy sur MinIO:9000) — sinon les liens d'aperçu et
  d'export ne s'ouvrent pas depuis l'app.
