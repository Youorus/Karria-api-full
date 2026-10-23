# Reprise — état de `karria_api_v2`

> Document de passation. À lire en premier par toute personne ou IA qui
> reprend le travail, avec `AGENTS.md`. Mis à jour à chaque fin de session.

**Dernière mise à jour : 2026-10-22**

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
| `identity` | fait | `POST /auth/email/start\|resend\|verify`, `POST /auth/social/google\|apple`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`, `DELETE /auth/me` |
| `devices`  | fait | `PUT/DELETE /devices/{installation_id}`, `POST /devices/me/test-notification` (local/test) ; service interne `SendPushToUser` (Expo) |
| `cv`       | fait | `GET /cv/me`, `GET/POST /cv/draft`, `POST /cv/draft/interpret`, `PUT /cv/draft/chapters/{c}`, `POST /cv/draft/finalize`, `POST /cv/photo`, `POST /cv/import`, `POST /voice/transcribe` |
| `offers`   | **en production** (ADR-0016/0017 validées par Marc le 2026-10-22, ADR-0018 « Proposée ») | `GET /job-matches…`, `POST /assistant/messages`, `GET/DELETE /assistant/memory`, `GET/PUT/DELETE /search/criteria`, `PUT /search/criteria/alerts`, routes internes `/internal/offers/targets` et `/batches` ; worker `scripts/worker.py` ; collecteur `python -m karria.collector` |

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
3. **Modules suivants**, à décider par Marc un par un : modèles de CV,
   génération de CV (PDF), abonnement Stripe.
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
