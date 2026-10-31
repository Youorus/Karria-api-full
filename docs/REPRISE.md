# Reprise — état de `karria_api_v2`

> Document de passation. À lire en premier par toute personne ou IA qui
> reprend le travail, avec `AGENTS.md`. Mis à jour à chaque fin de session.

**Dernière mise à jour : 2026-10-31**

## Où on en est

Nouvelle API de Karria, « très similaire à la V1, écrite parfaitement ».
Socle repris de `zoumani_api` (monolithe modulaire FastAPI, Tortoise +
aerich, uv, mypy strict, import-linter). La V1
(`~/PycharmProjects/karria_api`) sert de référence fonctionnelle, jamais
de code à copier.

L'app cliente est `~/WebstormProjects/karria_app`. Ses contrats en prose
font foi : `karria_app/docs/contrat-api-*.md`.

| Module     | État | Routes (préfixe `/api/v1`) |
| ---------- | ---- | -------------------------- |
| `health`   | fait | santé |
| `identity` | fait | `POST /auth/email/start\|resend\|verify`, `POST /auth/social/google\|apple`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me` (+ `ai_consent`), `DELETE /auth/me`, `GET/PUT/DELETE /auth/me/ai-consent` (ADR-0025) |
| `devices`  | fait | `PUT/DELETE /devices/{installation_id}`, `POST /devices/me/test-notification` (local/test) ; service interne `SendPushToUser` (Expo) |
| `cv`       | fait | `GET /cv/me`, `GET/POST /cv/draft`, `POST /cv/draft/interpret`, `PUT /cv/draft/chapters/{c}`, `POST /cv/draft/finalize`, `POST /cv/photo`, `POST /cv/import`, `POST /voice/transcribe` |
| `documents` | **en production** (ADR-0020 acceptée le 2026-10-24) | `GET /templates/`, `GET /templates/{id}`, `POST /templates/{id}/view\|preview`, `GET /templates/{id}/reviews`, `GET/POST /user-templates/me`, `GET/PUT /user-templates/me/selected`, `POST/GET /cv/me/generated-cvs`, `GET/DELETE /cv/me/generated-cvs/{id}`, internes `POST/GET /internal/templates` (jeton `DOCUMENTS__ADMIN_TOKEN`) ; travaux `documents.ingest_template`, `documents.explain_ats` ; commande `python -m scripts.templates` |
| `payments` | **en production, éteint** (ADR-0023 + **ADR-0024** « Proposées » ; sans `PAYMENTS__SECRET_KEY` → 503 et `enabled: false`) | `GET /payments/catalog`, `POST /payments/checkout-session`, `GET /payments/status/{id}`, `GET /payments/me`, `POST /payments/portal-session` (session requise), `GET /payments/return` (page publique), `POST /payments/webhook` (signature Stripe) |
| `support`  | **en production** (ADR-0026 « Proposée ») | `GET /support/me`, `GET /support/me/unread`, `POST /support/me/messages`, `POST /support/me/messages/media`, `POST /support/me/read`, WebSocket `GET /support/me/stream` ; équipe `GET /internal/support/conversations`, `GET /internal/support/conversations/{id}`, `POST …/{id}/messages`, `POST …/{id}/messages/media`, console `GET /internal/support/console` (jeton `SUPPORT__ADMIN_TOKEN`) |
| `offers`   | **en production** (ADR-0016/0017 validées par Marc le 2026-10-22, ADR-0018 et 0021 « Proposées ») | `GET /job-matches…`, `POST /assistant/messages`, `GET/DELETE /assistant/memory`, `GET/PUT/DELETE /search/criteria`, `PUT /search/criteria/alerts`, routes internes `/internal/offers/targets` et `/batches` ; worker `scripts/worker.py` ; collecteur `python -m karria.collector` |

### Session du 2026-10-31 (« Parler à l'équipe » — ADR-0026) — ÉCRIT PAR OPUS, À RELIRE PAR FABLE

**Demande de Marc** : messagerie entre une personne et les **humains** de l'équipe (pas
l'IA), comme `karria_api/app/support/` de la V1, mais en vrai temps réel (la V1 relisait
toutes les 60 s), avec vocaux et captures, push à chaque réponse, courriel à l'équipe.

**Livré** (module `support` + socle `shared/realtime`, fusionné sur `main` depuis la
branche `wip/parler-a-l-equipe`, supprimée) :

- `support_conversations` (une par compte) / `support_messages` (texte, ou clé d'un vocal
  ou d'une image dans le bucket privé `support`) — **migration 14**.
- Direct : WebSocket `/support/me/stream` (`Authorization: Bearer`, `?since=`), réveillé
  par `LISTEN`/`NOTIFY` PostgreSQL (`shared/realtime`, canal `karria_wakeups`), relecture
  de la base à chaque réveil et toutes les 20 s (un réveil perdu retarde, ne perd rien).
- Push `support` (contrat v1, canal `compte`, `karria://equipe`) sauf lecture < 45 s ou
  réponse non lue déjà en attente ; courriel `SUPPORT_NEW_MESSAGE` à
  `SUPPORT__TEAM_EMAIL` (défaut `mtakoumba@gmail.com`) une fois par rafale de 10 min.
- `shared/auth.TokenResolver` (jeton nu pour les WebSockets) branché sur `identity` ;
  `support` oublie fil et fichiers à la suppression du compte.
- Tests : `make check` vert (lint, mypy, frontières, 2 857 tests ; les dépôts PostgreSQL
  du module ne sont couverts qu'en intégration, ignorée sans Docker).

**Comment l'équipe répond** : ouvrir `https://api.karria.fr/api/v1/internal/support/console`,
coller le jeton `SUPPORT__ADMIN_TOKEN` (dans `/root/karria-api.env` sur `papex-new` ; gardé
dans l'onglet seulement). La liste montre les fils, « en attente » d'abord si on coche le
filtre ; ouvrir un fil le marque lu côté équipe ; répondre par écrit (ou joindre une
image). La personne voit la réponse en direct si l'écran est ouvert, sinon reçoit une
notification. Le courriel d'alerte mène directement au fil (`…/console#<id>`). Sans
console : `curl -H "Authorization: Bearer $JETON" https://api.karria.fr/api/v1/internal/support/conversations`
puis `POST …/conversations/<id>/messages` avec `{"body": "…"}`.

**Mise en production (faite le 2026-10-31)** : migration 14 appliquée puis bucket
`karria-production-support` créé **avant** le code, par conteneurs ponctuels sur l'image en
place avec `src/`, `scripts/`, `migrations/` du nouveau `main` montés depuis
`/root/karria-support-deploy` ; `SUPPORT__ADMIN_TOKEN` généré et ajouté à
`/root/karria-api.env` **et** à l'environnement Dokploy de l'API
(`application.saveEnvironment`, qui exige `createEnvFile`). Le push de `main` a déclenché
le webhook (les trois applications redéployées : `ffc17f2`, puis `f35aab4` « le flux accepte
puis ferme en 4401 » — refusé avant l'acceptation, le client ne voyait qu'un HTTP 403).
Vérifié : `/health/ready` 200 ; `/support/me` 401 sans jeton ; `/internal/support/conversations`
401 sans jeton, 200 avec ; console 200 ; `wss://…/support/me/stream` avec un faux jeton →
fermeture **4401** à travers Traefik. **Non vérifié** : un échange complet avec une vraie
session (pas de compte de test en production) — à faire depuis l'iPhone de Marc.

**Reste** : relecture par Fable (surtout `shared/realtime/postgres.py` et la boucle
`api/stream.py`) ; valider l'ADR-0026 (Marc) ; tests d'intégration des dépôts `support`
contre PostgreSQL ; au-delà de quelques centaines d'écrans ouverts, mesurer le coût des
relectures de 20 s.

### Session du 2026-10-30 (consentement à l'IA et lecture sans compte — ADR-0025) — en production

**Pourquoi** : revue App Store — 5.1.2 (i) (données envoyées à des tiers : accord explicite
avant le premier usage) et 5.1.1 (v) (Karria déjà refusée pour la connexion exigée au
lancement : les offres ne sont pas un contenu de compte).

**Livré** (commit `1192117` sur `main`, déployé par le webhook Dokploy ; **migration 13
appliquée avant le code** par un conteneur ponctuel avec le fichier monté ; `/health/ready`
200, API/worker/collecteur en service ; `GET /offers/feed` → 200 sans jeton) — ADR-0025
« Proposée » :

- `identity` : table `identity_ai_consents` (une ligne par compte, FK en cascade ; le
  retrait pose `revoked_at`), domaine `domain/ai_consent.py` — la **version se calcule**
  (`2026-10.<empreinte>`) depuis les fournisseurs réellement routés (`LLM__TASKS__*`,
  `LLM__FALLBACKS__*`, appelables seulement ; port `AiProviderCatalog` lu dans `Settings.llm`)
  et les usages (`AiPurpose` : assistant, transcription, cv_creation, cv_import,
  photo_enhancement, template_analysis). Un fournisseur de plus → version nouvelle →
  l'app redemande. Routes `GET/PUT/DELETE /auth/me/ai-consent`, champ `ai_consent` sur
  `GET /auth/me` ; erreurs `ai_consent_required` (403) et `ai_consent_version_mismatch` (409).
- `shared/auth` : **porte d'IA refusée par défaut** (`get_ai_consent_gate`,
  `AiConsentedPrincipalDep`), branchée dans `bootstrap/wiring.py` sur
  `identity.EnsureAiConsent` (7 branchements). Sous la porte : `POST /assistant/messages`,
  `/cv/draft/interpret`, `/cv/import`, `/voice/transcribe`, `/cv/photo` (si
  `enhance=true`), `/cv/me/generated-cvs` (le score ATS est expliqué par un modèle).
- `shared/http/ratelimit.py` : fenêtre glissante en mémoire, bornée ; `allow_anonymous`
  dans `shared/auth`. `offers` : `GET /job-matches/me`, `/me/filter-options`, `/{id}` sans
  session (`all_offers`, sans état ni journal ; `saved_only`/`unseen_only`/`unapplied_only`
  → 401 `account_required`), alias `/offers/feed`, `/offers/filter-options`, `/offers/{id}` ;
  240 lectures / 10 min par origine (`429 anonymous_rate_limited`). `documents` : galerie,
  détail, avis publics, 120 / 10 min. Tout ce qui écrit reste authentifié.
- Tests : `make check` vert (2 774 tests, 72 ignorés sans Docker), nouveau code à 100 % ;
  fixtures `ai_consented` (porte ouverte pour les tests de bout en bout) et `identity_env`
  (doublures `InMemoryAiConsentRepository`, `StaticAiProviderCatalog`).
- Docs : ADR-0025, README `identity`, `DEPLOIEMENT.md` § Consentement, `PLUS-TARD.md`
  (quotas anonymes par processus), contrats de l'app (`contrat-api-consentement-ia.md`
  nouveau ; offres, modèles, compte mis à jour). Côté app : `karria_app` commit `f8a6344`.

**Reste** : valider l'ADR-0025 (Marc) ; si l'API se réplique, compteur Redis derrière
`SlidingWindowLimiter` ; un test de charge du fil anonyme avant une campagne.

### Session du 2026-10-29, suite (paiements Stripe : « sans pub », cafés, modèles Premium) — en production, éteint

**Décision de Marc** : Stripe seul, par le navigateur, comme Zoumani (validée par Apple) ; pas
d'achat intégré, pas de RevenueCat, aucun SDK Stripe dans l'app. **ADR-0024** (Proposée) complète
l'ADR-0023 ; les deux attendent la validation de Marc.

**Livré** (commits `6445f4f`, `3c89c9c` sur `main`, déployés par Dokploy ; **migration 12
appliquée** en production le 29/10 ; les trois services en service, `/health/ready` 200) :

- Catalogue dans le domaine (`domain/catalog.py`) : sans pub **4,99 €/mois** sans engagement ;
  cafés **1 / 2 / 3 / 5 à 2,99 / 4,99 / 7,99 / 12,99 €** ; modèle Premium au prix de `documents`
  (port `TemplateStore` ← `documents.TemplateShop`). Prix Stripe retrouvés par `lookup_key`
  (`karria_ad_free_monthly`, `karria_coffee_{n}`, `karria_template_{uuid}`) et **créés par le
  code**, TTC : Marc ne saisit aucun produit.
- Tables `payments_accounts` (projection : client Stripe, statut, fin de période, résiliation,
  départ de la grâce), `payments_purchases` (uniques par session), `payments_webhook_events`
  (registre). Webhook idempotent (identifiant noté dans la transaction de l'effet), état relu
  chez Stripe, **grâce de 7 jours** après `invoice.payment_failed`, modèle remis par `grant`
  dans la même transaction. `ForgetUser` sur `AccountDeleted` : client Stripe supprimé
  (résiliation), projection et achats effacés.
- Page de retour `GET /payments/return` (Stripe refuse `karria://`) : revalide la destination
  et renvoie vers `karria://paiement/retour?status=…&session_id=…`, ce qui referme la session du
  navigateur de l'app. Interrupteur `PAYMENTS__IOS_ENABLED` / `ANDROID_ENABLED` exposé dans
  `enabled`. Sans clé : 503, `enabled: false`, et `/me` ne lit pas la base (déployable avant
  migration — c'est ce qui a été fait).
- `identity` expose `GetCurrentUser` / `UserNotFoundError` / `get_current_user_reader` ;
  `documents` expose `TemplateShop` / `PremiumTemplate` / `get_template_shop` ; `wiring.py` :
  6 branchements, 5 ponts.
- Tests : `make check` vert (2 721 tests, 72 ignorés = intégration sans Docker local) ; le
  nouveau code est à 100 % hors `infrastructure/repositories.py`, couvert par
  `tests/integration/test_payments_repositories.py` — **non exécuté ici** (Docker arrêté) : à
  lancer avec `make db-up` à la prochaine session.

**Pour Marc — à faire chez Stripe** (détail dans `DEPLOIEMENT.md` § Encaissements) : activer le
compte ; créer le webhook `https://api.karria.fr/api/v1/payments/webhook` (5 événements) ;
activer le portail client (résiliation, factures, moyen de paiement) et les e-mails de reçu ;
poser `PAYMENTS__SECRET_KEY`, `PAYMENTS__WEBHOOK_SECRET` (+ `PAYMENTS__IOS_ENABLED=true`,
`PAYMENTS__ANDROID_ENABLED=true`, `PAYMENTS__AUTOMATIC_TAX=false`) dans Dokploy pour l'API et
redéployer ; tester avec `4242 4242 4242 4242` puis passer en live. Valider ADR-0023/0024.

**Reste** : la réconciliation planifiée avec Stripe (`PLUS-TARD.md`), un courriel Karria sur
prélèvement en échec (idem), relire la carte de soutien de l'app sur le simulateur.

### Session du 2026-10-29 (relecture Fable avant lancement — défauts corrigés, mesures)

**Contexte** : relecture critique du travail d'Opus (offres, documents, logos, push,
AdMob, performance de l'app). Tout est commité sur `main` des deux dépôts.

**Défauts corrigés (par gravité)** :

1. **Bloquant — `payments` (commit `5b9b71c`, en production depuis le 26/10)** : routes
   sans authentification, `return_url` libre (relais d'hameçonnage), mode démonstration
   qui rendait « payé » pour tout `demo_…`, `os.getenv`, `HTTPException`, `make check`
   rouge. Réécrit en quatre couches (ADR-0023), session exigée, retour borné à karria.fr /
   `karria://`, pas de démonstration, tests. Sans clé en prod → 503 (voulu).
2. **Haut — dialogue** : Gemini omettait `criteria` sur **9 demandes sur 20** en répondant
   « c'est noté, je recherche… » — rien ne cherchait (mesuré en prod, `review_before`).
   `criteria` est maintenant exigé par le schéma, redemandé une fois s'il manque, et le
   domaine pose la question du métier au lieu d'une fausse confirmation.
3. **Haut — ingestion concurrente** : `UniqueViolation (source, source_id)` quand le
   balayage nocturne et le passage de 3 min se recouvrent (lot entier rejoué), et
   `UniqueViolation (key, domain)` dans `save_many` (entreprises du lot non rattachées).
   Insertion idempotente ; `save_many` rend l'identifiant qui a gagné et les offres s'y
   rattachent.
4. **Haut — `fetch_logos`** : `retry=2` sur un travail qui se re-diffère sous le même
   `queueing_lock` → `UniqueViolation (queueing_lock)`, travail bloqué « en cours »
   jusqu'à `retry_stalled`. Le retry est retiré (le périodique est la reprise).
5. **Moyen — documents** : la variante « optimisée ATS » proposée sans être mesurée (elle
   pouvait perdre des points) → rendue et notée, proposée seulement si elle gagne ; polices
   relues dans le bucket à chaque export et téléchargeables depuis Internet pendant un
   rendu → cache par processus, `download=False` au rendu, `https` seul ; PDF > 20 Mio
   tronqué au lieu d'être refusé ; aucune borne à l'ingestion (pages, surface, images) ;
   lecture pdfminer sur la boucle ; panne Gotenberg = version `FAILED` définitive (→ se
   retente) ; débordement sur deux pages rendu comprimé ; identifiants de formes épuisables,
   famille de police non échappée, rôle multiple non-chaîne, schéma LLM non borné.
6. **Sécurité — logos** : la résolution DNS est épinglée (la requête joint l'IP vérifiée,
   nom dans `Host` et SNI) : plus de fenêtre de rebinding.
7. **Perf — recherche** : `find_many(light=True)` sans description pour le rappel (1 500
   candidats = 3 Mo → 60 Ko) et les comptages d'élargissement (jusqu'à 9 par tour) ;
   `DispatchAlerts` sans N+1 sur le fil.

**Mesures en production (20 demandes réelles, juge informé, 2 passages)** — voir
`docs/DEPLOIEMENT.md` § Mesure :

| | avant (`review_before`) | après (`review_after`) |
|---|---|---|
| demandes cherchables au 1er tour | **11 / 20** | **20 / 20** (schéma seul, aucun second appel) |
| précision@10 jugée | 97,0 % (98/101 ; FT 94 %, HW/Indeed/LinkedIn 100 %) | 96,2 % (177/184 ; FT 99 %, Indeed 93 %, HW 91 %, LinkedIn 100 %) — équivalent, sur 20 demandes au lieu de 11 |
| recherche totale p50 / p95 | 217 / 688 ms | 241 / 329 ms |
| dont base (lecture des candidats) p50 / p95 | 84 / 129 ms | 108 / 154 ms (20 demandes ; le coût est la matérialisation ORM, pas le transfert : gain en mémoire, pas en latence médiane) |
| dont index Qdrant p50 / p95 | 46 / 397 ms | 42 / 90 ms |

Lecture honnête : le gain net est la **cherchabilité** (9 demandes sur 20 ne cherchaient pas) ;
la précision est inchangée ; la latence p95 baisse (688 → 329 ms) mais les deux passes ne
portent pas le même nombre de demandes et le serveur est partagé — à relire sur une semaine.
`scripts/offers_eval.py` accepte désormais `find_many(light=…)`.

Corpus au 29/10 : **196 196 offres actives canoniques** (FT 158 k, Indeed 21 k, HelloWork
13,5 k, LinkedIn 3 k), index Qdrant à l'identique, outbox vide, collecte FT toutes les 3 min
et JobSpy en continu (collecteur, worker, API en service).

**Pour Marc** : tranché le 29/10 au soir (Stripe seul, ADR-0024) ; les modifications non
commitées de l'app ont été reprises et commitées — voir `karria_app/docs/REPRISE.md`.

### Session du 2026-10-25 (contrat des notifications push, ADR-0022)

- **`shared/push`** : `PushKind` (9 types), `PushMessage` validé à la construction ;
  `data` = `v` "1", `type`, `id`?, `count`?, `campaign`?, `url` ; hors `data` :
  `channelId` = `categoryId` (`offres` / `documents` / `compte`), `badge` 1 pour les offres.
  Miroir côté app : `karria_app/docs/contrat-notifications.md`.
- **Émetteurs migrés** : alertes d'`offers` (1 offre → `offer`+`id`, plusieurs →
  `offers_feed`+`count` ; avant : `NEW_OFFERS` sans `url`, l'app n'ouvrait rien),
  route d'essai (`offers_feed`, `campaign: test`), `scripts/push_campaign.py`
  (`offers_feed`, `campaign: v1-retour-2026`). Ports `UserNotifier.notify(user_id, message)`
  et `SendPushToUser.execute(user_id=, message=)`.
- **Aucun push CV/export** n'existe (exports synchrones) : noté dans `PLUS-TARD.md`.
- `make check` vert (2 609 tests), nouveau code couvert à 100 %.

### Session du 2026-10-25 (logos d'entreprise partout, LinkedIn, fenêtre France Travail) — en production

**Livré** (commits `3bead7b` → `6e88c04` sur `main` ; le webhook Dokploy **déploie de
nouveau** à chaque `push`) — ADR-0021 « Proposée » :

- **Entreprises** (`offers_companies`, migrations 9 à 11) : clé de nom (pliée, sans forme
  juridique) + domaine ; chaque offre porte `company_id` et une copie `company_logo_hash`.
  Un domaine n'est retenu que si le nom (ou son sigle) s'y lit (`plausible_domain`) et s'il
  n'est pas porté par plus de 3 entreprises ; un logo V1 seulement s'il vient d'une page
  entreprise LinkedIn, d'un logo carré Indeed ou du propre domaine (`trusted_v1_logo`).
- **Logos** : téléchargés une fois (httpx, 15 s/requête, 5 s DNS, 60 s/entreprise, jamais
  d'adresse privée, 2 requêtes et 250 ms par site), jugés (taille, rapport, aplat), mis au
  carré, WebP 128/256, bucket **public** `karria-production-logos`
  (`https://storage.karria.fr/karria-production-logos/<condensat>/<taille>.webp`,
  `immutable`, 138 Mo au 25/10). Travail `offers.fetch_logos` (toutes les 5 min + dès qu'un
  lot montre une entreprise), sous bail, **priorité aux entreprises vues récemment**
  (`last_offer_at`). Mesuré : entreprise née d'une offre → logo prêt en **60 s (p50), 72 s
  (p95)** sur 17 entreprises. Worker à 3 places. Logos V1/icône partagés par plus de 5
  entreprises retirés après chaque passage.
- **Contrat** : `company.logo` (128) et `company.logo_large` (256) = notre adresse, jamais
  celle de la source ; `null` → initiales. App : `861d974` (expo-image, cache mémoire+disque,
  fondu 180 ms, repli sur les initiales).
- **V1** : `public.companies` (dump `karria_essentiel_2026-09-12`) restauré dans une base
  de travail locale (`docker` `karria-v1-scratch`, port 55432 — à supprimer quand inutile),
  exporté en JSONL (`~/karria-archive/v1-companies/`, copie sur le serveur dans
  `/root/karria-v1-companies/`) : **182 865 lignes → 170 983 entreprises** ; 81 168 offres
  rattachées, 8 574 entreprises nouvelles.
- **Couverture des logos** (offres actives canoniques) — avant : adresse de la source
  servie telle quelle ; après (25/10, 17 h 20, rattrapage en cours) :
  France Travail 3,3 % → **15,9 %** (27,7 % des offres qui nomment l'employeur ; 43 % des
  offres FT sont anonymes), Indeed 56,7 % → **55,8 %**, HelloWork 92,2 % → **82,5 %**
  (bandeaux trop allongés et vignettes refusés), LinkedIn **93,3 %**. 5 506 entreprises ont
  un logo (5 162 images distinctes).
- **LinkedIn** dans la collecte : 40 recherches/h de 25 annonces, 12 s ± 50 %, disjoncteur
  429/999 sur plusieurs passages (30 min doublé jusqu'à 6 h), erreurs JobSpy relevées
  (`SiteBlockedError`). 184 offres LinkedIn en quelques heures, 21 dédoublonnées avec France
  Travail et Indeed, aucun blocage.
- **France Travail** : fenêtre sûre au changement d'heure (enveloppe des heures murales),
  fenêtre vide ignorée — les deux 400 du 25/10 à 1 h 02 et 1 h 05 UTC.
- **Incidents du jour** : ~10 min de 502 (bucket `logos` absent au démarrage : provisionner
  **avant** de déployer) et worker en boucle (httpx n'était qu'en dépendance de test). Les
  deux sont dans `DEPLOIEMENT.md`.

**En cours** : le conteneur `karria-logo-backfill` (`scripts.companies fetch-logos`, budget
10 h, ~160 entreprises/min avec le worker) traite les ~87 700 entreprises restantes (surtout
V1 sans offre) ; il s'arrête seul. `docker logs karria-logo-backfill | grep logos_fetched`.

**Reste** :
1. Revoir `scripts.companies shared-logos` après le rattrapage : remplir
   `PLACEHOLDER_HASHES` si une image générique apparaît.
2. `PLUS-TARD.md` § Entreprises et logos : SVG, rafraîchissement, DNS épinglé, LinkedIn et la
   longue traîne des cibles.
3. Valider l'ADR-0021 (Marc).

### Session du 2026-10-24, suite (module `documents` : modèles de CV, exports, ATS) — en production

**Livré** (commits `a3f6cd9`, `c0338e5` sur `main`, déploiement Dokploy manuel de l'API
et du worker, migration 8 appliquée, bucket `karria-production-templates` créé) :

- **Module `documents`** (README du module, ADR-0020 « Acceptée » + § « Mise en œuvre ») :
  ingestion d'un PDF sans PyMuPDF (pdfplumber + pypdfium2), rôles des lignes par
  `template_extract`, spécification canonique, porte de qualité PDF ≥ 0,90 + planchers,
  aperçus (PNG, WebP) et **BlurHash**, versions immuables (`version` +1 à chaque analyse).
- **Polices** : incorporée si licence + couverture du français, sinon même famille libre
  (Google Fonts / Fontsource, gardée dans `templates/fonts/…`), sinon remplaçante mesurée
  (34 familles curées) ; instances statiques ; rapport par version ; incorporées au DOCX.
- **DOCX** fidèle (interligne exact compensé, espaces avant, taquets, colonnes en tableau,
  piles d'en-tête, frises/pastilles/filets DrawingML ancrés, puces = listes Word, polices
  incorporées).
- **Poste visé** toujours sous le nom (synthétisé dans le style du modèle s'il manque ;
  omis s'il est vide). Flux de création vérifié : la question « quel métier tu vises ? »
  remplit `identity.headline`, l'interprète le redemande s'il manque — rien à corriger.
- **ATS** lu par pdfminer ; plafond de la variante optimisée **0,08 em** (mesuré) ;
  explication rédigée par le worker (`ats_explain`).
- **Gotenberg 8.38.1** (`karria-gotenberg`, 1 Gio, interne) ajouté à `karria-donnees`
  (Dokploy mis à jour + `docker compose up -d gotenberg`, les autres services intacts).
- **Variables** posées (API + worker + `/root/karria-api.env`) :
  `DOCUMENTS__GOTENBERG_URL`, `DOCUMENTS__ADMIN_TOKEN` (valeur dans
  `/root/karria-documents-admin.token`, 600). `STORAGE__PUBLIC_ENDPOINT_URL` **non posée**.
- **Modèles en production** : Meridian v1 **publié** (PDF 0,934 ; DOCX 0,881), Minimalist v1
  **publié** (0,916 ; 0,836), Marketing v1 **en revue** (0,822 ; 0,755 — défauts du modèle,
  Aileron servie en CC0). Sources dans `/root/templates/` sur le serveur.
- **Test de fumée en production** (pile complète en processus, session substituée, CV
  « Marc Test », supprimé ensuite) : exports 201 en 1,5 à 3,9 s, aperçu 0,6 s ; ATS PDF/DOCX
  Meridian 80/96 (variante ATS 85/96), Minimalist 87/96 (variante 83/96) — après le correctif `c0338e5` (style du lien LinkedIn) ; explications
  `ready`. Fichiers : scratchpad `prod-smoke/` (hors dépôt).
- **Tests** : `make check` vert, 2 410 tests ; `documents` à 100 % (lignes + branches)
  hors `repositories.py`/`queue.py` (tests d'intégration). Couverture par chemin
  (`--cov=src/karria/modules/documents`) : la forme pointée échoue à cause de numpy.
- **Contrat app** : `karria_app/docs/contrat-api-modeles.md` passé à « servi », commit
  `4d5c64d` sur `karria-app-full` main.

**Reste (par priorité)** :
1. **Marc** : DNS `storage.karria.fr` → 128.140.80.204, domaine Dokploy (Let's Encrypt) sur
   le service MinIO port 9000 de `karria-donnees`, puis
   `STORAGE__PUBLIC_ENDPOINT_URL=https://storage.karria.fr` dans l'API et le worker
   (et `/root/karria-api.env`) et redéployer. Sans cela, les URL signées (vignettes,
   aperçus, exports) visent `karria-minio:9000` et ne s'ouvrent pas depuis l'app.
2. Revue Fable du module ; `suggest_ats_safe` se fie aux intitulés espacés et peut
   proposer une variante qui ne gagne rien (Minimalist : 87 → 83 par l'ordre de lecture
   de pdfminer) — mesurer les deux et ne proposer que si le score monte.
3. Puces du DOCX en glyphes (plus petites que les pastilles du modèle), Marketing en revue,
   seconde page : voir `PLUS-TARD.md` § Modèles de CV.
4. Le lot `~/Desktop/Modele de CV/v2/cv1…cv10` : `scripts.templates submit … --inline`.

### Session du 2026-10-24 (mode du fil, espace Anthropic, modèles de CV — conception et preuve)

**Livré et déployé** (commits `febf602`, `267f5dd` sur `main`, déploiement Dokploy manuel
de `karria-api-ujqvaj`, vérifié en production) :

- **Item 0 — `GET /job-matches/me?mode=all_offers|search`** (et `/me/filter-options`) :
  `all_offers` montre tout le corpus **sans toucher aux critères** (recherche et alertes
  restent actives) ; `search` sans critères retombe sur `all_offers` ; absent = déduit des
  critères ; la réponse rend le mode réellement servi et `criteria_summary`. L'app ne doit
  plus faire `DELETE` puis `PUT /search/criteria` pour « mettre en pause » (contrat
  `karria_app/docs/contrat-api-offres.md` mis à jour, **non commité** côté app : le dépôt
  de l'app avait déjà des modifications en cours d'une autre main).
- **Item 1 — `LLM__PROVIDERS__ANTHROPIC__WORKSPACE_ID`** : envoyé en en-tête
  `anthropic-workspace-id`. Vide → Anthropic **hors routage** (journal
  `llm.provider_disabled` à la construction du routeur, qui est paresseuse), sans erreur ;
  une route `LLM__TASKS__*=anthropic:…` sans lui bloque le démarrage en nommant la
  variable. Vérifié dans le conteneur de prod : adaptateurs `['gemini']`, aucun problème.
  **Reste :** l'identifiant d'espace de travail (Marc) dans `.env` et
  `/root/karria-api.env`, puis redéployer.

**Modèles de CV (item 2) — conçu et prouvé, rien dans le dépôt hors docs :**

- **ADR-0020** (Proposée) : spécification canonique tirée du PDF (extraction déterministe
  + étiquetage des lignes par vision via `shared/llm`), deux moteurs (HTML → PDF par
  Chromium/Gotenberg, DOCX natif python-docx), métrique de fidélité et seuils, score ATS
  déterministe expliqué par le modèle, adaptation à une offre conçue, routage
  (`template_extract`, `ats_explain`, plus tard `cv_tailor`), tableau V1 → v2.
- **Contrat** : `karria_app/docs/contrat-api-modeles.md` (galerie, détail, aperçus, choix,
  exports PDF/DOCX, objet `ats`) — non commité côté app (même raison).
- **Preuve de concept** (hors dépôt, venv jetable) :
  `~/Documents/Karria-modeles-poc-2026-10-24/` — `code/` (extract, label, spec, view,
  render_html, render_docx, fidelity, ats, explain, run_*), `sources/` (PDF Canva),
  `extraction/`, `resultats/<modèle>/` (spec.json, sample_render.pdf, cv_camille.pdf/.docx,
  ats_*.json, côte-à-côte `sbs*.png`, aperçus) et `resultats/summary.json`.
  Fidélité PDF : **Meridian 0,924, Minimalist 0,922** (seuil 0,90 atteint), Marketing 0,705
  (police Aileron absente + erreur dans le modèle → refusé, comme prévu). DOCX 0,81 / 0,73
  (sans ornements). V1 0,32 ; option B (pdf2docx) 0,38–0,44. ATS du CV fictif : Meridian
  PDF 86 / DOCX 92, Minimalist 91 / 93 ; variante « optimisée ATS » 93 / 94.
- **Relancer la preuve** : recréer à côté de `code/` les dossiers `fonts/` (Raleway,
  Montserrat variables + Lato, Google Fonts) et `fonts/static/` (instances statiques par
  `fontTools.varLib.instancer`, voir `code/polices-utilisees.txt`), `src/`, `extract/`,
  `out/poc/` ; un venv avec pymupdf, python-docx, numpy, pillow, fonttools ; Gotenberg
  local (`docker run -p 3005:3000 gotenberg/gotenberg:8.38.1`, le conteneur
  `karria-gotenberg-poc` tourne encore sur la machine de Marc, image avec polices,
  `code/gotenberg-fonts.Dockerfile`). Puis `spec.py`, `run_sample.py`, `run_cv.py` ;
  `label.py` et `explain.py` se lancent avec `uv run` depuis `karria_api_v2` (clé Gemini
  du `.env`).
- **Décisions ouvertes pour Marc** : valider l'ADR-0020 ; créer **un** module `documents`
  (ou autre découpage) ; accepter `python-docx`, `pdfplumber`, `fonttools`, `numpy`
  (PyMuPDF **écarté : AGPL**) ; ajouter Gotenberg 8.38.1 (1 Gio) à `karria-donnees` ;
  politique pour les polices non libres ; variante ATS proposée (défaut = fidélité).
- **Prochaine étape proposée** : après validation — module et migrations (modèles,
  versions, possession, choix, exports), ingestion en commande + travail de file, porte de
  qualité, routes du contrat ; puis ornements DOCX et le lot `~/Desktop/Modele de CV/v2`.

### Session du 2026-10-23, suite (élargissements, journal d'apprentissage, migration V1)

- **Fil sans plafond bas** : rappel 1 500 candidats, fil jusqu'à 1 000 entrées
  (écriture en un lot). **Élargissements** (`domain/widening.py`, ADR-0018) :
  Karria compte dans l'index ce que rapporterait chaque relâchement (rayon,
  départements voisins, toute la France, contrat, temps, expérience, site,
  intitulés voisins tirés des écartées de la porte) et propose les 3 meilleurs
  avec leurs gains **comptés** ; une réponse rapide reprise telle quelle
  s'applique sans le modèle ; propositions gardées avec la conversation
  (migration 6). Réponse `/assistant/messages` : `widenings[]`. Mesuré en prod
  (`scripts/widening_eval.py`, 13 demandes) : recherche 55 ms à 1,9 s (froid),
  comptage 0,1 à 2,4 s ; « cuisinier CDI Lyon » 22 offres → toute la France
  +346. Les voisines d'intitulé se limitent désormais à la tête du rappel.
- **Journal d'apprentissage** (ADR-0019, migration 7) : chaque affichage avec
  sa place, chaque bascule d'état, `dwell_ms`, et l'instantané des traits.
  Contrat `seen`/`interaction` étendu. Rien ne s'entraîne encore
  (`scikit-learn`/`lightgbm` : accord de Marc attendu).
- **Plongement `t3`** : plus de ligne des traits dans `embedding_text` ;
  ré-indexage prod lancé par `reconcile` (54 000 points).
- **Décisions appliquées** : `LLM__TASKS__CV_INTERPRET=gemini:gemini-3.5-flash`
  en prod ; utilisateur de test supprimé.
- **Migration V1** : `scripts/migrate_v1.py` (export / convert / import dry-run)
  et `scripts/push_campaign.py` (jamais d'envoi sans `--send`) — les jetons V1
  sont des FCM bruts, inutilisables par Expo (voir `PLUS-TARD.md`). Import en
  prod **non lancé**.
- **Reporté** (consigne de sobriété) : filtres étendus + facettes du fil (item 1,
  retiré par Marc), re-jugement de la précision après `t3`, nouvelle passe
  red-team, suite d'évaluation large des élargissements.
- **Déploiement** : le webhook GitHub vise `admin.papex.fr` dont le DNS pointe
  sur l'ancien serveur → déployer à la main (`docs/DEPLOIEMENT.md`).

### Session du 2026-10-23 (porte d'intitulé, couche LLM, agent Karria, mise en production)

- **Porte d'intitulé** : mots de niveau déterministes (`domain/titles.py`)
  + `excluded_titles` rendus par le modèle dans l'appel de dialogue.
  Précision@10 jugée **82 % → 91,4 %** (FT 93 %, HelloWork 90 %, Indeed
  93 %) ; « cuisinier temps plein France » 10/10.
- **Couche LLM** `shared/llm` (ADR-0018) : Gemini, Anthropic, OpenAI,
  compatible OpenAI, REST sans SDK ; routage par tâche
  (`LLM__TASKS__*`, `LLM__FALLBACKS__*`, `LLM__PROVIDERS__*`), validé au
  démarrage ; `cv` et `offers` migrés. Banc d'essai (`scripts/llm_bench.py`)
  : gemini-3.8-flash gardé partout ; 3.5-flash recommandé pour
  `cv_interpret` (6/6, 1,3 s) — **à confirmer par Marc**.
- **Agent Karria** : constitution versionnée (1.0, tutoiement), mémoire
  par personne (`KarriaMemory`, migration 5, visible/effaçable),
  pré-classifieur et garde de sortie déterministes, `intent` dans la
  réponse ; suite d'attaques 36 cas **36/36**
  (`docs/evals/assistant_redteam.json`, relance :
  `ASSISTANT_REDTEAM_LIVE=1 pytest tests/integration/test_assistant_redteam_live.py`).
- **Classifieurs sur plongements** (`docs/evals/classifiers_e5.md`) : temps
  de travail et télétravail utiles (88 %/95 %, couverture 80 %/93 % à 0,8),
  contrat moyen (76 %, couverture 51 %), expérience inutile ; **fuite** :
  `embedding_text()` écrit les traits dans le texte plongé. Trois décisions
  pour Marc (voir `PLUS-TARD.md`).
- **Production** (`docs/DEPLOIEMENT.md` § État réel) : API, worker et
  collecteur en service sur Dokploy ; `https://api.128-140-80-204.sslip.io`
  et `api.karria.fr` ; migrations 3-5 appliquées ; 60 463 offres en base
  dont 54 072 actives canoniques, index Qdrant `karria-production-offers`
  = 54 072 points (import de l'évaluation sans recalcul, fantômes purgés,
  `reconcile` à 0) ; smoke test de bout en bout réussi (connexion e-mail,
  `/auth/me`, fil, tour de recherche « cuisinier CDI Lyon » → 20 offres des
  trois sources). Pile d'évaluation arrêtée (base `karria_eval` et
  collections `karria-eval-staging-*` gardées).
- **Non fait** : la relecture par Opus du travail (Tâche 3) — risques
  listés dans le rapport de session ; l'enrichissement du résidu.

### Session du 2026-10-22 (France Travail, collecte continue, planificateur) — validée par Marc

- **France Travail est la source n°1** (`collector/france_travail.py`) :
  OAuth2, pagination 150/3 150, partition par département, fuseau de Paris
  (mesuré : les `Z` de l'API sont de l'heure de Paris), passage toutes les
  3 min + balayage nocturne. Identifiants dans `.env` local
  (`COLLECTOR__FRANCE_TRAVAIL_*`) et `/root/karria-collector.env` (600) sur
  le serveur ; jamais affichés.
- **Contrat de remise** (v1, additif) : indices structurés (contrat, temps,
  expérience, salaire, coordonnées, instants, `expired`). Ils priment sur
  le texte. Règle nouvelle : **temps partiel demandé → offre muette écartée**.
- **Planificateur piloté par la demande** (`domain/steering.py`, migration
  4) : cibles canoniques terme × département × contrat, liens
  personne ↔ cible, `search_terms` et `preferences` dans les critères,
  validation des termes contre les titres réels, essai des cibles,
  priorité (demande, manque, rendement, ancienneté, redondance FT), cadence
  adaptative ; côté collecteur : gigue, budget horaire, zéro silencieux.
- **Règle** : une conversation ne cherche jamais en direct
  (`tests/unit/offers/test_no_live_search.py`, import-linter).
- **Mesuré** (serveur d'évaluation, index 41 000 offres canoniques) :
  précision@10 jugée 82 % (FT 83 %, HelloWork 85 %, Indeed 78 %) ;
  parution → indexée p95 14 min 28 s (sous charge de balayage) ; alerte
  dans la seconde qui suit l'indexation ; recherche p95 98 ms. Détail et
  limites : ADR-0017.
- **Reste pour la suite** (voir le rapport de session et `PLUS-TARD.md`) :
  la porte d'intitulé et les mots de niveau (« chef cuisinier »), la boucle
  de vérification de vie France Travail, la boucle des synonymes, la couche
  LLM multi-fournisseurs (ADR-0018 à venir) et la relecture du travail
  d'Opus.
- **Environnement d'évaluation** (`ssh papex-new`, partagé avec Papiers
  Express — ne jamais toucher à `papex*`) : conteneurs `karria-eval-apiserver`,
  `karria-eval-worker`, `karria-eval-collector` (image `karria-eval-api`
  construite depuis `/root/karria-eval/code`), base `karria_eval`, sorties
  dans `/root/karria-eval/out/` (`eval_ft2.jsonl`, `complex.jsonl`,
  `seed.jsonl`, `catalogue.jsonl`).

Qualité au 2026-10-23 : `make check` est vert. ~1 600 tests passent et 22
sont ignorés (tests en direct et MinIO absent en local). 100 % sur le
domaine et l'application des modules métier.

Tout est commité et poussé sur `main` de `Youorus/Karria-api-full`
(autorisation explicite de Marc le 2026-10-22 pour la mise en production ;
le `push` déclenche le déploiement Dokploy). Hors de ce cadre, la règle
reste : ne commiter que sur demande.

## Décisions clés (détail dans `docs/adr/`)

- **Sessions (ADR 0010)** : modèle Zoumani.
  - Jeton d'accès de 15 minutes.
  - Jeton de rafraîchissement opaque, haché, renouvelé à chaque usage, glissant sur 365 jours.
  - La réutilisation d'un jeton déjà renouvelé révoque toute la session (60 s de grâce).
  - La session n'est perdue qu'à la déconnexion.
- **Google et Apple (ADR 0011)** :
  - vérification RS256 contre les clés publiques du fournisseur (JWKS) ;
  - contrôle de `aud`, `iss`, `exp` et du nonce (sha256 pour Apple) ;
  - le compte est rattaché par `sub` ;
  - un compte = un e-mail prouvé, sans nom ni prénom.
- **Appareils (ADR 0012)** :
  - un appareil par (utilisateur, installation) ;
  - aucune IP stockée, le pays vient de l'en-tête `CF-IPCountry`.
- **Gemini (ADR 0013)** :
  - accès par un port et l'API REST, sans SDK ;
  - réponses à schéma imposé, revalidées par Pydantic ;
  - le texte de l'utilisateur est traité comme une donnée, jamais comme une consigne.
  - Modèles : `gemini-3.8-flash` (texte, audio, extraction) et `gemini-3.1-flash-image` (photo).
- **CV (ADR 0014)** : sections en JSONB ; la photo est référencée par `image_id`, jamais par une URL signée.
- **Suppression de compte (ADR 0015)** :
  - suppression physique ;
  - l'événement `AccountDeleted` est consommé par `devices` et `cv` via des ponts dans `bootstrap/wiring.py` ;
  - la raison du départ est gardée de façon anonyme (`identity_departure_feedback`).

## Lancer en local

```bash
cd ~/PycharmProjects/karria_api_v2
unset VIRTUAL_ENV            # un venv d'un autre projet perturbe uv
make up                      # Postgres + MinIO + migrations + buckets
uv run uvicorn karria.main:app --reload --host 0.0.0.0 --port 8001
```

- Port **8001** : le 8000 est pris par l'API V1 locale.
- `.env` est hors git et déjà rempli : Gemini (`GEMINI__API_KEY`) et les
  identifiants OAuth Google (`SOCIAL__*`, repris de la V1, bundle
  `com.karria.app`).
- Ne jamais afficher ces valeurs.
- Mail en mode `console` : le code de connexion s'affiche dans le journal du serveur.

## Ce qui reste (par priorité)

1. **Tester sur le simulateur** :
   - connexion Google et Apple réelle (Marc doit la faire, une IA ne peut pas taper) ;
   - parcours CV complet ;
   - photo améliorée ;
   - import.
2. **Valider l'ADR-0018** (couche LLM, agent Karria, enrichissement) et
   trancher : `gemini-3.5-flash` pour `cv_interpret` ; `scikit-learn` dans
   l'image ; retrait des traits du texte plongé (ré-indexage) ; champs qui
   valent un appel au modèle. Puis la relecture du travail (Tâche 3) et la
   purge des offres inactives en production.
3. **Paiements** : poser les clés Stripe et tester (voir la session du 2026-10-29, suite) ;
   modules suivants à décider par Marc un par un.
4. **Points listés dans `docs/PLUS-TARD.md`** :
   - purge planifiée ;
   - limiteur de débit et cache JWKS multi-réplicas (Redis) ;
   - livraison durable des événements ;
   - durée de conservation des raisons de départ ;
   - routes d'édition directe du CV ;
   - chapitre `projects`.
5. **Hébergement et déploiement** : `docs/DEPLOIEMENT.md` décrit la mise
   en service du moteur d'offres (trois processus) ; le reste est un squelette.

## Règles de travail (Marc)

- Ne jamais commiter ni pousser sans demande explicite.
- Économiser les tokens : agir avec précision et lancer des agents
  seulement pour de vrais chantiers. À la limite de session, s'arrêter
  proprement et reprendre à la session suivante.
- E-mail administrateur : `mtakoumba@gmail.com`.
- Ne jamais publier ni afficher de secret (`~/karria-archive` en contient en clair).
- Mettre ce fichier à jour à chaque fin de session.
