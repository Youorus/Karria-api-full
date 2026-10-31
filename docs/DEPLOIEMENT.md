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
  karria-worker-y9xkmo:latest aerich upgrade         # ou : python -m scripts.offers reconcile
```

L'image est la même pour les trois applications ; depuis le 2026-10-26 seul le
tag `karria-worker-y9xkmo:latest` subsiste (`docker image ls | grep karria`
donne l'identifiant courant — l'API tourne sur le même identifiant). Les
conteneurs ponctuels s'appuient dessus, pas sur `karria-api-ujqvaj:latest`,
qui n'existe plus.

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

## Entreprises et logos (ADR-0021, 2026-10-25)

- **Migration** 9 (`entreprises_et_logos`) : `aerich upgrade`.
- **Bucket** `karria-production-logos`, **public en lecture** (seul de son
  espèce) : `python -m scripts.storage_provision`. **À faire AVANT de
  déployer** le code qui déclare un bucket : le démarrage de l'API vérifie
  chaque bucket du registre et refuse de démarrer sans lui (constaté le
  2026-10-25 : 10 min de 502, le temps de provisionner avec la nouvelle
  image). De même, une dépendance d'exécution ne doit pas vivre dans le
  groupe `dev` (httpx, même jour : worker en boucle de redémarrage). Servi par
  `https://storage.karria.fr/karria-production-logos/<condensat>/<128|256>.webp`
  (`STORAGE__PUBLIC_ENDPOINT_URL` doit être posée, sinon les adresses visent
  l'hôte interne).
- **Worker** : 3 places (`WORKER_CONCURRENCY`) ; le travail `offers.fetch_logos`
  tourne toutes les 5 min.
- **Amorçage**, une fois, dans l'ordre (conteneurs ponctuels avec
  `/root/karria-api.env`) :
  1. `python -m scripts.companies import-v1 /in/companies.jsonl.gz` — l'export
     de `public.companies` de la V1, tiré d'une base de travail où le dump
     `karria_essentiel_2026-09-12.sql.gz` a été restauré ;
  2. `python -m scripts.companies backfill` (puis `--all`) — rattache les offres en base ;
  3. `python -m scripts.companies fetch-logos --budget 14400 --concurrency 32` —
     le rattrapage des logos, en parallèle du worker (bail commun, pas de double prise) ;
  4. `python -m scripts.companies coverage` — la couverture par source.
- **Collecteur** : LinkedIn est dans les sites par défaut (`COLLECTOR__SITES` le
  retire au besoin) ; ses réglages propres (`COLLECTOR__SITE_HOURLY_BUDGETS`,
  `…_SITE_PAUSE_SECONDS`, `…_BLOCKED_COOLDOWN_SECONDS`) ont des défauts prudents.

## Encaissements Stripe (ADR-0023, ADR-0024 — 2026-10-29)

- **Migration 12** (`encaissements_stripe` : `payments_accounts`,
  `payments_purchases`, `payments_webhook_events`) : `aerich upgrade` dans un
  conteneur ponctuel (voir § État réel). Sans clé Stripe, le code ne lit pas ces
  tables : il peut être déployé avant la migration, mais la migration doit être
  passée **avant de poser la clé**.
- **Variables de l'API** (le worker n'en a pas besoin aujourd'hui — aucun
  travail de file ne touche Stripe) : `PAYMENTS__SECRET_KEY` (clé secrète
  `sk_test_…` puis `sk_live_…`), `PAYMENTS__WEBHOOK_SECRET` (`whsec_…` du point
  de terminaison), `PAYMENTS__RETURN_URL` (destination par défaut, l'app passe
  `karria://paiement/retour`), `PAYMENTS__IOS_ENABLED` / `PAYMENTS__ANDROID_ENABLED`
  (`true` ; `false` cache tout paiement dans l'app sans livraison),
  `PAYMENTS__AUTOMATIC_TAX` (`false` tant que Stripe Tax n'est pas activé dans
  le tableau de bord). `APP__PUBLIC_URL=https://api.karria.fr` compose la page
  de retour (déjà posée).
- **Chez Stripe** (Marc, tableau de bord) : le compte activé ; le **webhook**
  `https://api.karria.fr/api/v1/payments/webhook` abonné à
  `checkout.session.completed`, `customer.subscription.created`,
  `customer.subscription.updated`, `customer.subscription.deleted`,
  `invoice.payment_failed` ; le **portail client** activé (résiliation
  immédiate ou en fin de période permise, historique des factures, mise à jour
  du moyen de paiement) ; les **e-mails de reçu** activés. **Aucun produit ni
  prix à créer** : le code les crée par `lookup_key` au premier achat
  (`karria_ad_free_monthly`, `karria_coffee_1|2|3|5`, `karria_template_<uuid>`),
  TTC. Les faire en mode test d'abord, puis refaire webhook et clés en mode live.
- **Vérifier** : `GET /api/v1/payments/catalog` avec une session → `enabled`
  à `true` ; un paiement test (carte `4242 4242 4242 4242`) → le webhook
  répond 200 dans le tableau Stripe et `GET /payments/me` passe `ad_free.active`
  à `true` ; `docker logs` de l'API : `payments.webhook_received`,
  `payments.subscription_synced` / `payments.purchase_recorded`.
- **Couper d'urgence** : `PAYMENTS__IOS_ENABLED=false` (ou retirer la clé :
  503 partout) et redéployer l'API — aucune livraison de l'app.

## Consentement à l'IA et accès sans compte (ADR-0025 — 2026-10-30)

- **Migration 13** (`consentement_ia` : `identity_ai_consents`) : `aerich upgrade`
  dans un conteneur ponctuel (voir § État réel), **avant** de déployer le code :
  sans la table, `GET /auth/me` répond 500 et toute route d'IA refuse.
- Aucune variable nouvelle. La liste des fournisseurs montrée à la personne se
  lit dans `LLM__TASKS__*` / `LLM__FALLBACKS__*` : changer un fournisseur change
  la version du consentement, et l'app redemande l'accord — c'est voulu.
- **Vérifier** : `curl https://api.karria.fr/api/v1/offers/feed?limit=1` → 200
  sans jeton (`mode: all_offers`) ; `.../templates/` → 200 ; `.../auth/me/ai-consent`
  → 401 sans jeton ; avec une session, `GET /auth/me` porte `ai_consent`.
- Les quotas anonymes (240 lectures / 10 min pour les offres, 120 pour les
  modèles, par origine `X-Forwarded-For`) sont en mémoire, par processus
  Gunicorn : le total réel est multiplié par le nombre de workers.

## « Parler à l'équipe » (ADR-0026 — 2026-10-31)

Dans cet ordre, **avant** de déployer le code :

1. **Migration 14** (`parler_a_l_equipe` : `support_conversations`,
   `support_messages`) : `aerich upgrade` dans un conteneur ponctuel (voir
   § État réel). Sans les tables, `GET /support/me` répond 500.
2. **Bucket** `karria-production-support` (privé) :
   `python -m scripts.storage_provision` dans le même conteneur ponctuel. Sans
   lui, l'API **refuse de démarrer** (vérification du registre, cf. logos).
3. **Variables de l'API** : `SUPPORT__ADMIN_TOKEN` (jeton long et aléatoire,
   `openssl rand -hex 32` ; vide → les routes de l'équipe et la console
   répondent 401) ; `SUPPORT__TEAM_EMAIL` facultatif (défaut
   `mtakoumba@gmail.com` — **jamais** `contact@papiers-express.fr`).
   Le SMTP est celui déjà posé (`MAIL__*`).

Puis déployer l'API (et le worker, même image). Rien à régler côté Traefik :
le WebSocket `wss://api.karria.fr/api/v1/support/me/stream` passe tel quel.

- **Répondre** : `https://api.karria.fr/api/v1/internal/support/console`,
  coller le jeton (gardé dans l'onglet, `sessionStorage`). Le courriel envoyé
  à chaque nouveau message y mène directement (`#<id du fil>`).
- **Vérifier** : `/health/ready` 200 ; `GET /api/v1/support/me` avec une
  session → 200 ; `GET /api/v1/internal/support/conversations` sans jeton →
  401, avec → 200 ; `docker logs` de l'API : `realtime.listening` à la première
  ouverture d'un flux, `support.stream_opened`.
- **Couper d'urgence** la console : vider `SUPPORT__ADMIN_TOKEN` et redéployer.

## Mesure de la recherche en production (relecture du 2026-10-29)

`python -m scripts.offers_eval --runs 2 --judge --only <indices>` dans un
conteneur ponctuel (image ci-dessus, `--env-file /root/karria-api.env`,
`-v /root/karria-eval/out:/out --out /out/<nom>.jsonl`). Rien n'est écrit en
base. Vingt demandes jugées coûtent une vingtaine d'appels Gemini.
