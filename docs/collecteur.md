# Le collecteur d'offres et son contrat avec l'API

Le collecteur (`src/karria/collector/`) interroge les sites d'offres et
remet ce qu'il trouve à l'API. Il vit dans ce dépôt et dans la même image,
mais c'est un **autre processus** et une **autre application Dokploy** : il
ne voit ni la base, ni le stockage, ni la file, ni les modèles. Ce document
fait foi pour les deux rives ; la décision et ses raisons sont dans
l'ADR-0016.

| Rive | Ce qu'elle possède |
|---|---|
| Collecteur | **France Travail** (API officielle, incrémentale, la source n°1 — ADR-0017), JobSpy (Indeed, HelloWork), mandataires sortants, pause avec gigue, budget horaire et disjoncteur par site, détection du « zéro silencieux », correctifs d'analyse propres à un site, mise au format de remise |
| API (module `offers`) | les cibles (canoniques, dérivées des recherches des personnes, planifiées par la demande et le rendement), l'atterrissage des lots, la déduplication, la normalisation, les plongements, l'index, les alertes |

**Règle :** une conversation avec Karria ne déclenche jamais une requête à
France Travail ni à un site. La recherche lit l'index ; au plus, elle rend
une cible due, que le collecteur prend à son passage.

import-linter garde la frontière : le collecteur n'importe que la
journalisation du socle ; l'API n'importe jamais le collecteur ni JobSpy.

## Les deux boucles du collecteur

- **France Travail**, toutes les 3 minutes (`COLLECTOR__FRANCE_TRAVAIL_POLL_SECONDS`) :
  les annonces créées depuis le dernier passage, avec 5 minutes de
  recouvrement ; un balayage par département de la veille chaque nuit
  (2 h UTC). Aucune cible : la source est nationale et incrémentale. Les
  lots partent **sans** `X-Target-Key`. Les instants de l'API sont l'heure
  de Paris marquée `Z` (mesuré) : le collecteur les convertit en vrai UTC.
- **JobSpy**, toutes les 10 minutes (`COLLECTOR__INTERVAL_SECONDS`) : le
  cycle ci-dessous.

## Le cycle d'un passage JobSpy

1. `GET /api/v1/internal/offers/targets?limit=30` → les cibles dues, **par
   priorité** (demande, manque, rendement, ancienneté, redondance avec
   France Travail). Elles sont **louées** 30 minutes : un second appel ne
   les rend pas.
2. Pour chaque cible, chaque site : une requête JobSpy (pause de 2 s entre
   deux requêtes au même site ; au bout de 3 échecs consécutifs, le site est
   laissé de côté pour le reste du passage).
3. `POST /api/v1/internal/offers/batches` avec le lot de la cible — **même
   vide** : il clôt la cible côté API. Sans lot, la cible redevient due à la
   fin du bail.

L'API atterrit le lot tel quel (bucket `landing`, 90 jours), diffère son
ingestion (`offers.ingest_batch`) et répond `202`. Une panne d'ingestion se
rejoue depuis l'atterrissage (`scripts/offers.py replay`), sans redemander
quoi que ce soit au collecteur.

## Authentification

`Authorization: Bearer <jeton>`, le même secret dans les deux applications :
`COLLECTOR__TOKEN`. Sans jeton configuré côté API, les routes internes
répondent `401 collector_token_invalid` à tout le monde. Comparaison à temps
constant.

## `GET /internal/offers/targets` → `200`

```jsonc
{
  "handoff_version": 1,
  "targets": [
    {
      "key": "alternance developpeur web|lyon",   // à renvoyer dans X-Target-Key
      "search_term": "alternance développeur web",  // 1 à 4 mots + le contrat : jamais le texte d'une personne
      "location": "Lyon",                          // la ville du géocodeur, ou « France »
      "distance_km": 30,                           // null : le rayon du site
      "last_found": 42                             // le dernier passage : zéro ici alors que c'était 42 = page changée
    }
  ]
}
```

`limit` : 1 à 100 (30 par défaut).

## `POST /internal/offers/batches` → `202 { "key": "collector/…" }`

- Corps : JSONL **compressé gzip**, une annonce par ligne, au plus 2 000
  annonces et 16 Mo.
- En-têtes : `Content-Type: application/gzip`, `X-Handoff-Version: 1`
  (obligatoire), `X-Target-Key` (la cible servie).
- Erreurs : `401 collector_token_invalid`, `422 handoff_version_unsupported`,
  `422 batch_too_large`, `422 target_unknown`.

Une ligne :

```jsonc
{
  "source": "hellowork",            // obligatoire : indeed | hellowork | linkedin | glassdoor | google
  "source_id": "hw-84683722",       // obligatoire, stable d'un passage à l'autre
  "title": "Serveur H/F",           // obligatoire
  "description": "…",               // Markdown léger, "" si inconnue
  "company_name": "Temporis",       // les champs suivants sont facultatifs
  "company_logo_url": null,        // le logo chez la source : l'API le télécharge une fois (ADR-0021)
  "company_url": null,              // souvent la page de l'employeur SUR la source
  "company_website": null,          // son vrai site, quand la source le donne (Indeed : company_url_direct)
  "location_label": "Vienne, 38, France",
  "date_posted": "2026-10-22",      // AAAA-MM-JJ
  "job_types": ["temporary", "fulltime"],
  "is_remote": false,
  "work_from_home_type": null,
  "job_level": null,
  "salary_min": 14.54,
  "salary_max": 14.54,
  "salary_interval": "hourly",      // yearly | monthly | weekly | daily | hourly
  "salary_currency": "EUR",
  "job_url": "https://www.hellowork.com/fr-fr/emplois/84683722.html",
  "job_url_direct": null,
  // Indices structurés, quand la source les donne (France Travail). Ils
  // priment sur la lecture du texte ; une valeur hors du domaine vaut absent.
  "contract_type": "cdi",           // cdi | cdd | interim | alternance | stage | freelance | saisonnier
  "schedule": "temps_plein",        // temps_plein | temps_partiel
  "experience": null,               // debutant | junior | confirme | senior
  "experience_years": 1.0,          // années demandées, l'API en déduit le niveau
  "salary_text": "Mensuel de 2300.0 Euros",
  "latitude": 46.478, "longitude": 0.520, "postal_code": "86340",
  "posted_at": "2026-10-22T10:28:37+00:00",   // à la seconde, en UTC vrai
  "updated_at": "2026-10-22T10:30:00+00:00",
  "expired": false,                 // true : la source a retiré l'annonce (source + source_id suffisent)
  "raw": { }                        // la ligne d'origine (ROME compris), pour rejouer une normalisation
}
```

`source` accepte aussi `france_travail`. Une ligne `{"source": "france_travail",
"source_id": "215NPDX", "expired": true}` désactive l'offre connue et la
retire de l'index.

L'API ignore les clés qu'elle ne connaît pas et écarte, sans faire échouer
le lot, une ligne inexploitable (source inconnue, ni identifiant ni titre,
JSON invalide). Un type inattendu vaut « absent ». Le collecteur peut donc
ajouter un champ ou un site sans casser l'API ; changer le **sens** d'un
champ impose `X-Handoff-Version: 2` et une ADR.

Les deux copies du contrat : `src/karria/collector/contract.py` (écriture)
et `src/karria/modules/offers/domain/handoff.py` (lecture). Un test
(`tests/unit/test_collector_contract.py`) vérifie qu'elles s'accordent.

## Ce que JobSpy impose (v1.4.0, vérifié dans son code)

- HelloWork ne rend la description qu'avec `fetch_description=True` (une
  requête de plus par annonce) ; sans, titre et ville seulement.
- HelloWork ne comprend pas « France » comme lieu : sans lieu, il cherche
  dans tout le pays.
- Le rayon est en miles.
- Le 2026-10-22, HelloWork a changé le balisage de ses cartes (`<span>` au
  lieu de `<p>`) : JobSpy rendait zéro annonce sans erreur. Corrigé par
  `collector/sources.py::patch_hellowork`. C'est le genre de panne qui doit
  rester de ce côté-ci.
- `fetch_description` vaut pour tous les sites, LinkedIn compris
  (`linkedin_fetch_description` en est l'ancien nom, déprécié depuis 1.2.0).
- JobSpy **range les erreurs d'un site dans `frame.attrs["errors"]`** et rend
  un tableau vide : un LinkedIn bloqué (429, 999, « blocked? ») ressemblerait à
  « rien trouvé ». `sources.raise_for_errors` les relève : `SiteBlockedError`
  (blocage, avec les annonces déjà lues) ouvre le disjoncteur du site ;
  `SiteError` (échec sans résultat) compte comme un échec ordinaire.
- **LinkedIn** (ajouté le 2026-10-25) : sans compte ni mandataire, depuis le
  serveur, 25 annonces par recherche dont 21 à 24 avec description et logo,
  ~28 s par recherche (une requête de description par annonce). D'où ses
  réglages propres : 25 annonces par recherche, **40 recherches par heure**,
  12 s ± 50 % de pause, et un disjoncteur qui le laisse de côté 30 min après un
  blocage, doublé à chaque blocage consécutif jusqu'à 6 h (sur plusieurs
  passages). Le zéro silencieux le surveille comme les autres. Les cibles
  sont les mêmes pour tous les sites : le planificateur ne connaît pas les
  sites, LinkedIn sert les cibles les plus prioritaires de chaque passage
  tant que son budget le permet.
- La mention « CDI » est perdue par JobSpy sur les deux sites ; l'API la
  retrouve dans le texte, et déduit un CDI d'une carte HelloWork sans type
  de contrat.

## Ce que France Travail impose (API « Offres d'emploi v2 », vérifié le 2026-10-22)

- 10 appels/s par client ; `range` de 150 au plus, index final 3 149 ; une
  fenêtre plus large se partitionne par département puis par moitiés de temps.
- `dateCreation` / `dateActualisation` : heure de Paris marquée `Z`, et les
  bornes `minCreationDate` / `maxCreationDate` sont lues dans le même fuseau.
- `GET /offres/{id}` rend 204 pour une annonce retirée.
- `dureeTravailLibelleConverti` est vide sur **47 %** des annonces (surtout
  intérim et non-salarié) ; le temps reste alors inconnu.
- ~24 000 annonces créées par jour de semaine ; 110 s et 317 requêtes pour
  relire une journée.

## Mesurer la fraîcheur (SLO de l'ADR-0017)

```sql
-- parution → ingérée → indexée, sur les annonces France Travail de la dernière heure
select count(*),
  percentile_cont(0.95) within group (order by extract(epoch from (first_seen_at - published_at)))::int as p95_parution_ingeree,
  percentile_cont(0.95) within group (order by extract(epoch from (indexed_at - first_seen_at)))::int as p95_ingeree_indexee,
  percentile_cont(0.95) within group (order by extract(epoch from (indexed_at - published_at)))::int as p95_parution_indexee
from offers_offers
where source = 'france_travail' and published_at > now() - interval '1 hour'
  and indexed_at is not null and duplicate_of is null;
-- parution → fil (alertes)
select percentile_cont(0.95) within group (order by extract(epoch from (f.created_at - o.published_at)))::int
from offers_feed_entries f join offers_offers o on o.id = f.offer_id
where f.origin = 'alert' and o.published_at > now() - interval '1 hour';
```

Objectifs : parution → indexée ≤ 10 min (p95), parution → fil ≤ 15 min (p95).

## Lancer

```bash
uv run python -m karria.collector               # les deux boucles (France Travail 3 min, JobSpy 10 min)
uv run python -m karria.collector --once        # un passage de chaque
uv run python -m karria.collector --ft-sweep 1  # amorçage : relit la veille France Travail par département
```

Variables : `COLLECTOR__API_URL` (ex. `http://karria-api:8000/api/v1`),
`COLLECTOR__TOKEN`, `COLLECTOR__FRANCE_TRAVAIL_CLIENT_ID` /
`_CLIENT_SECRET` / `_SCOPE` / `_TOKEN_URL` / `_SEARCH_URL` (sans
identifiants, la source est absente et annoncée), et facultativement
`COLLECTOR__PROXIES` (liste JSON), `COLLECTOR__SITES`,
`COLLECTOR__RESULTS_WANTED` (60), `COLLECTOR__HOURS_OLD` (72),
`COLLECTOR__TARGETS_PER_RUN` (30), `COLLECTOR__PAUSE_SECONDS` (2),
`COLLECTOR__PAUSE_JITTER` (0,3), `COLLECTOR__SITE_HOURLY_BUDGET` (200),
`COLLECTOR__SITE_FAILURE_LIMIT` (3), `COLLECTOR__SILENT_ZERO_LIMIT` (3),
`COLLECTOR__FRANCE_TRAVAIL_POLL_SECONDS` (180), `_OVERLAP_SECONDS` (300),
`_STARTUP_HOURS` (2), `_SWEEP_DAYS` (1), `_SWEEP_HOUR_UTC` (2),
`COLLECTOR__LOG_LEVEL`. Déploiement : `docs/DEPLOIEMENT.md`.
