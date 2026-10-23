# ADR-0016 — Le moteur d'offres : collecte découplée, base de vérité, index hybride, porte d'intitulé

**Statut :** Proposée — 2026-10-22 (à valider par Marc)
**Contexte :** module `offers` et composant `karria.collector`

## Le problème

Le cœur de Karria : une personne dit ce qu'elle cherche (« serveur à
Paris », ou bien plus précis), et voit dans son fil les offres actives qui
y répondent ; Karria la prévient quand il en arrive de nouvelles.
Contraintes posées par Marc :

- **50 000 à 70 000 annonces collectées par jour**, à dédoublonner
  **avant** tout plongement ; ne jamais re-plonger un contenu déjà plongé
  (la V1 a brûlé son crédit OpenAI en ré-indexant tout, 5 à 8 €/jour les
  jours de refonte contre 0,5 € en régime normal) ;
- ingestion ≥ 5 offres/s soutenue, marge ×3 ; stock actif de plusieurs
  centaines de milliers à plus d'un million de vecteurs ; recherche p95
  < 800 ms hors tour de dialogue ;
- un serveur de 8 vCPU (EPYC Milan, AVX2 sans AVX-512) **partagé** avec
  Papiers Express ; aucun GPU ;
- la qualité des meilleurs produits : le bon métier, au bon endroit, avec
  le bon contrat, rien de hors sujet.

Tout ce qui suit a été **mesuré** le 2026-10-22 sur ce serveur et sur des
offres réelles (10 614 annonces Indeed et HelloWork collectées le jour même,
507 requêtes métier × ville), avec `scripts/offers_eval.py`.

## Ce qu'on décide

### 1. Deux processus, un contrat : le collecteur ne touche à rien

La collecte (JobSpy, mandataires, politesse envers les sites, analyse
propre à chaque site) est un **composant à part**, `src/karria/collector/`,
dans le même dépôt et la même image, lancé par une autre commande
(`python -m karria.collector`) dans une autre application Dokploy.
Il ne reçoit **aucun** secret de l'API (ni base, ni stockage, ni modèle) :
une adresse et un jeton de service. import-linter le vérifie dans les deux
sens : le collecteur n'importe ni `karria.modules`, ni `karria.bootstrap`,
ni la base, ni le stockage, ni la file ; rien côté API n'importe le
collecteur, ni JobSpy.

Le contrat (`docs/collecteur.md`) tient en deux routes internes,
authentifiées par `Authorization: Bearer <COLLECTOR__TOKEN>` :

- `GET /api/v1/internal/offers/targets?limit=30` — les cibles dues
  (terme, lieu, rayon), **louées** 30 minutes pour qu'un second passage ne
  les reprenne pas ;
- `POST /api/v1/internal/offers/batches` — un lot JSONL gzip, une annonce
  par ligne (clés de `RawPosting`), en-têtes `X-Handoff-Version: 1` et
  `X-Target-Key`. L'API l'atterrit **tel quel** dans le bucket `landing`
  (90 jours, de quoi rejouer une normalisation) et diffère son ingestion
  (`offers.ingest_batch`) : la requête reste courte.

Pourquoi un envoi HTTP plutôt qu'un dépôt direct dans MinIO : le
collecteur parle à des sites tiers, c'est le processus le plus exposé ; ne
lui donner ni identifiants S3 ni accès base borne ce qu'une compromission
atteint. Un bannissement d'IP, une page HelloWork modifiée ou un JobSpy
cassé n'appauvrissent qu'un passage — vérifié le jour même : HelloWork a
changé le balisage de ses cartes vers 9 h 30 (JobSpy rendait zéro annonce
sans erreur) ; le correctif (`collector/sources.py::patch_hellowork`) est
resté de ce côté-ci du contrat.

Prudence envers les sites : pause de 2 s entre deux requêtes au même site,
disjoncteur par site après 3 échecs consécutifs, lot remis même vide (il
clôt la cible). LinkedIn est écarté (limite agressive, pas de description
sans une requête par annonce). HelloWork exige `fetch_description=True`
(sinon titre et ville seulement) et ne connaît pas « France » comme lieu.

### 2. PostgreSQL est la vérité ; la déduplication passe avant tout calcul

`IngestPostings` travaille **par lot**, du moins cher au plus cher :

1. `(source, identifiant)` — une requête pour le lot. Une annonce revue
   **à l'identique** (même condensat) ne coûte qu'un `UPDATE last_seen_at`,
   sans géocodage ni normalisation ;
2. condensat SHA-256 du texte canonique (titre, employeur, description
   pliés) — une requête pour le lot, et dans le lot lui-même ;
3. SimHash 64 bits sur les 3-grammes de mots, distance de Hamming ≤ 3,
   candidats par quatre bandes de 16 bits **et** par employeur (index
   composites `(company_key, simhash_bandN)`), sur 45 jours.

Mesuré : 31 annonces/s au premier remplissage (géocodage BAN à froid), 74/s
cache chaud, 113/s en renormalisation ; **la cible de 5/s est tenue ×6**.
Sur les 10 614 annonces du premier jour : 1 400 revues sous le même
identifiant, 344 doublons exacts, 414 quasi-doublons (le même poste sur
Indeed et HelloWork, ou retouché), **8 397 canoniques — 79 %** (un premier
remplissage ; la part d'un jour suivant est consignée dans `REPRISE.md`).

Une grappe de doublons vit aussi longtemps que sa **dernière** parution :
une annonce reprise vingt jours plus tard ailleurs prolonge la canonique.
Une offre expire 30 jours après sa dernière (re)publication.

### 3. L'index est une projection, synchronisée par une boîte d'envoi transactionnelle

Une ligne `offers_index_outbox` s'écrit **dans la transaction** qui crée
ou modifie l'offre. `SyncIndex` (travail `offers.sync_index`, chaque
minute et dès qu'un lot a inséré) la vide par lots de 64, et **déduit**
l'opération de l'état de l'offre au moment du traitement — ce qui rend le
traitement idempotent et l'ordre indifférent :

- plus indexable (expirée, devenue doublon, disparue) → retrait du point ;
- indexable et déjà plongée **sur ce condensat à cette version**
  (`indexed_hash`, `indexed_version`) → attributs seulement, aucun calcul ;
- sinon → plongement, puis écriture.

C'est la garantie qui manquait à la V1 : un contenu n'est plongé qu'une
fois par (condensat, version du modèle et du texte). Une renormalisation
des 4 661 offres HelloWork a réécrit leurs attributs en 9 s, **sans un
seul plongement**. Un changement de modèle se fait par une commande
explicite (`scripts/offers.py reconcile`), jamais au démarrage ; la même
commande, planifiée chaque nuit, rattrape ce que l'outbox aurait manqué.

### 4. Plongements : multilingual-e5-base quantifié int8, sur un texte court

| Modèle (TEI, ONNX, CPU) | annonces/s à 600 car. | requête |
|---|---|---|
| bge-m3 fp32 (mesure de la veille) | ~1,8 | 45 ms |
| multilingual-e5-base fp32 | 9,7 | 25 ms |
| **multilingual-e5-base int8** | **15,3** (31,6 à 300 car.) | **9 ms** |
| multilingual-e5-small int8 | 44,8 | 4 ms |

Retenu : **e5-base int8**, 768 dimensions. La quantification dynamique par
canal (`scripts/quantize_onnx.py`) se fait une fois, hors projet ; TEI
charge le dossier local `/opt/karria/models/e5-base-int8`. Le texte plongé
est court (titre, employeur, lieu, contrat, 450 premiers caractères de la
description, ~600 caractères) : le coût est linéaire en longueur et la
suite d'une annonce (avantages, présentation de l'employeur) tire le vecteur
vers un « employeur générique ». **De bout en bout (plongement + écriture
Qdrant + base) : 20,7 offres/s soutenues**, ×4 la cible.
e5-small irait trois fois plus vite ; il n'est pas retenu faute de mesure de
qualité à son avantage, et parce que le dense n'est plus ce qui décide
(§ 6). C'est la première piste si le volume triple.

### 5. Recherche hybride : dense + deux BM25 calculés par Qdrant, fusion RRF

Qdrant v1.20 calcule lui-même le BM25 (`qdrant/bm25`, racinisation
française, accents pliés — vérifié : « developpeur », « développeurs »,
« DEVELOPPEURS » se rejoignent). Deux vecteurs creux par offre : **titre
seul** et **corps** (titre, employeur, 3 000 caractères), IDF côté serveur.
Le dense est quantifié int8 en RAM, original sur disque (~0,8 Go de RAM
par million de points au lieu de 3 Go), re-score sur l'original. Une
requête : trois branches filtrées (contrat, télétravail, temps, expérience,
salaire, rayon géographique ou départements), fusion **RRF pondérée**
(1 ; 1 ; 0,5) côté serveur, 300 candidats. La segmentation est réglée à la
création de la collection (la V1 en avait laissé s'accumuler 111).

Le filtre de l'index et la règle du domaine (`SearchCriteria.accepts`)
disent la même chose ; un test les confronte. Deux règles mesurées :

- « uniquement en télétravail » écarte une offre muette sur le sujet ; une
  offre « télétravail complet » hors de la zone n'est retenue que si la
  personne a parlé de télétravail (une caisse en alternance à Venerque
  remontait pour « caissière à Lille ») ;
- pour l'alternance et le stage, une offre **sans contrat lisible est
  écartée** : ces annonces le disent toujours (pour « chargé de recrutement
  en alternance », 4 offres sur 10 sans contrat, aucune n'en était une).
  Pour un CDI, l'absence ne prouve rien : JobSpy perd la mention « CDI »
  sur les deux sites — sauf sur HelloWork, où une carte sans type de
  contrat est un CDI (son seul libellé que JobSpy ne traduit pas).

### 6. Pas de reclasseur neuronal : une porte d'intitulé, décidée par le domaine

L'architecture convenue prévoyait un reclassement d'une petite tête de
candidats. **Mesuré, il est écarté :**

| | précision@10 | recherche p50 / p95 |
|---|---|---|
| hybride + `mmarco-mMiniLMv2` int8 (30 candidats, seuil 0,15) | **≈ 58 %** (168/287, jugé à la main) | 413 / 506 ms |
| hybride + **porte d'intitulé** (300 candidats) | **95,6 %** (197/206, jugé à la main) | **61 / 77 ms** |

Le reclasseur léger note « Assistant RH » 0,95 et « ÉLECTRICIEN CONFIRMÉ »
0,00 pour « électricien » ; celui qui classe juste (`bge-reranker-v2-m3`)
lit 40 intitulés en **6,7 s** sur ce CPU. La similarité dense rapproche des
domaines, pas des métiers (« serveuse » ↔ « chef de partie » : 0,86 ;
↔ « serveur » : 0,87).

La porte (`domain/titles.py`) : une offre n'entre dans un fil que si son
titre contient **tous** les mots d'un des intitulés compris par Karria
(« chef de rang » exige « chef » et « rang »), après une racinisation
grossière et symétrique (accents, féminin, pluriel). La compréhension —
synonymes, féminin, « hôte de caisse » pour « caissière » — est le travail
du modèle de langage en amont ; la décision est une règle explicable du
domaine. C'est la leçon de la V1, où 60 à 80 % du fil était hors sujet tant
qu'une porte « taxonomie = 0 » n'écartait pas les offres sans lien de
métier. Le score ([0, 1]) combine le rang de l'intitulé reconnu (le métier
dit, ou un synonyme) et la place dans le rappel hybride. Le prix : une offre
au titre inattendu (« Barber » pour « coiffeuse ») n'entre pas ; c'est un
rappel perdu, pas une erreur montrée. Le reclasseur et son conteneur sont
retirés (−2,4 Go de RAM).

### 7. Dialogue : Gemini comprend, le domaine décide

Un tour = un appel Gemini Flash (`GEMINI__TEXT_MODEL`), sortie à schéma
imposé (ADR-0013) : réponse, critères complets (le modèle reçoit les
critères courants et les complète), `off_topic`, 0 à 4 **suggestions** de
réponses rapides. Le lieu nommé est géocodé (BAN) **avant** que le domaine
décide (`decide`) : il faut un métier et un lieu (ou « toute la France »,
ou « en télétravail ») pour chercher, rien d'autre ; une seule question
courte sinon. Par défaut, aucune question quand on peut chercher : les
suggestions affinent sans formulaire. Mesuré : 35/35 demandes comprises et
cherchées au premier tour ; 4,3 s p50, 5,1 s p95 (≈ 1 000 jetons en
entrée, 350 en sortie). Les derniers critères de chaque personne sont
gardés (`offers_criteria`), modifiables sans conversation
(`PUT /search/criteria`).

### 8. Alertes : matching inversé, dédoublonné, borné

Les critères de chaque personne ont leur vecteur dans une collection
Qdrant `criteria`, avec de quoi pré-filtrer par lieu (étendue, départements,
centre). À chaque lot nouvellement **plongé**, chaque offre fraîche
(≤ 3 jours) cherche ses lecteurs : similarité ≥ 0,78, filtre de lieu large,
puis `accepts` et la porte d'intitulé. Une offre n'entre qu'une fois dans
un fil (unicité `(personne, offre)`), une offre écartée jamais ; une
notification groupée part au plus toutes les 2 h et 4 fois par jour, par
`devices.SendPushToUser` (branché par `bootstrap`, règle 4). Une panne des
alertes ne bloque jamais l'index.

### 9. Pilotage de la collecte par la demande

`RefreshScrapeTargets` (chaque heure) dérive les cibles de tous les
critères — deux intitulés par personne, son lieu et son rayon, préfixés
« alternance » ou « stage » quand c'est le seul contrat voulu — plus un
socle de 30 métiers sur toute la France. Une cible demandée par plusieurs
personnes pèse plus et passe en premier ; elle se recollecte toutes les
6 h (le socle : 24 h) et s'éteint après 15 jours sans demande. **Une
recherche qui trouve moins de 10 offres rend ses cibles dues tout de
suite** : le collecteur les prend au passage suivant, les alertes
préviennent quand les offres arrivent. La V1 générait toutes les
combinaisons mots-clés × villes, et sa génération de requêtes n'était
finalement appelée nulle part.

### 10. File durable : procrastinate, sur PostgreSQL

Les travaux (`offers.ingest_batch`, `offers.sync_index`,
`offers.refresh_targets`, `offers.expire`, `offers.reconcile`) vivent dans
les tables `procrastinate_*` de la même base : ni Redis à opérer, ni
éviction silencieuse (la V1 perdait des travaux ARQ sous `allkeys-lru` et
en empilait 80 000, la même offre 670 à 3 921 fois). Chaque différé porte
un verrou de file (`queueing_lock`) et chaque travail périodique un verrou
d'exécution. Le worker est un processus à part (`scripts/worker.py`), même
image ; son schéma s'applique par `scripts/queue_schema.py`, comme
`aerich upgrade`, jamais au démarrage.

## Modèle d'échelle et de coût

| | mesuré | à 70 000/jour |
|---|---|---|
| Ingestion (dédup + normalisation) | 31–113 annonces/s | < 40 min/jour de CPU d'un cœur |
| Plongement + index | 20,7 offres/s | si 40 % nouvelles : 28 000 → **23 min/jour** à pleine charge |
| Marge sur 5 offres/s | ×4,1 (index), ×6 (ingestion) | ×3 demandé : tenu |
| Recherche (hors dialogue) | p95 77 ms | — |
| Stock actif (30 j après la dernière parution) | — | 600 000 à 850 000 points |
| RAM Qdrant (dense int8 + HNSW) | — | ~0,9 Go par million ; creux et originaux sur disque (~3 + 3 Go) |
| PostgreSQL | ~5 Ko par offre | ~5 Go par million de lignes |
| Coût par jeton | 0 (plongement et recherche locaux) | 0 € |
| Gemini | ≈ 1 000 + 350 jetons par tour | proportionnel aux conversations, pas au corpus |

La part réellement nouvelle d'un jour courant reste à mesurer sur la durée
(le premier jour en dit 79 %, parce qu'il remplit). Le plafond est le CPU
partagé : à 70 000 annonces dont 100 % nouvelles, 56 min/jour de plongement
— encore tenable ; au-delà, e5-small (×3) avant tout matériel.

## V1 → v2

| Pièce de la V1 | v2 | Pourquoi |
|---|---|---|
| Score hybrid_v3.6 (somme pondérée de 8 signaux) | **abandonnée** | des poids non mesurés que personne n'osait toucher ; remplacés par filtres durs + porte + rang de rappel |
| Porte anti-hors-sujet (taxonomie = 0 et sémantique < 0,72/0,76) | **reprise, refaite** | l'idée a sauvé la V1 (21–37 % → 81–95 % de cohérence) ; ici une règle d'intitulé explicable |
| Classification ROME | **abandonnée** | n'était plus utilisée (champs retirés, `rome_match` toujours nul) |
| Extraction d'acronymes des mots-clés | **abandonnée** | le modèle de langage rend les sigles (« QHSE », « CACES ») dans `keywords` |
| Rejets durs contrat et zone (−2,0) | **reprise** | en filtre de l'index et règle du domaine, identiques et testés |
| Correspondance d'expérience (France Travail) | **refaite** | lue dans le texte de l'annonce, filtre seulement si l'offre dit un niveau |
| Analyse du salaire + pénalité de plafond | **refaite** | brut mensuel lu dans la source ou le texte ; filtre « au moins », pas de pénalité |
| Retour d'expérience (mots aimés/rejetés, 90 j) | **abandonnée pour l'instant** | 10 profils en production, aucun signal ; l'écart est gardé et ne revient jamais |
| Plafond de rappel 500 → 5 000 | **refaite** | 300 candidats fusionnés, filtrés dans l'index ; plus de requête « diversité » à part |
| Requête de diversité (hors France Travail) | **abandonnée** | la fusion lexicale + dense et la dédup inter-sites la rendent inutile |
| Workers profil→offres et offre→profils | **refaits** | recherche à la demande + matching inversé à l'indexation |
| « Ne jamais baisser un score » à l'upsert | **abandonnée** | gardait des offres hors sujet visibles ; un recalcul remplace le fil, sauf ce que la personne a pris en main |
| Purges (obsolètes, géo, hors sujet) avec protection des offres manipulées | **reprise** | `entries_to_remove` : sauvegardée, ouverte ou candidatée ne disparaît jamais |
| Ré-indexation du corpus au démarrage | **abandonnée** | interdite (AGENTS.md § 6.4) ; versions d'index par condensat |
| Plongement OpenAI de l'annonce entière | **remplacé** | e5-base int8 local sur 600 caractères : 0 €, 15 offres/s |
| Heuristiques de texte plongé (tags, mots-clés, titre ×3) | **abandonnées** | code mort qui tirait les vecteurs vers l'informatique |
| Une tâche ARQ par offre, sans identifiant | **remplacée** | outbox par lots + procrastinate avec verrous de file |
| Segments Qdrant non réglés (111) | **corrigé à la source** | réglés à la création de la collection |
| Expansion géographique (pgeocode, dépt → villes) | **refaite** | BAN + rayon `geo_radius` ou départements dans le filtre ; plus de « zéro match » silencieux |
| Agent d'intention Gemini Pro + réflexion | **refait** | Flash sans réflexion, schéma imposé, critères complétés tour après tour ; mesuré 35/35 |
| Gardien d'onboarding, chiens de garde | **abandonnés** | un compte n'a plus de profil à matcher ; la recherche est synchrone et à la demande |
| Seuils push (≥ 0,75, 60 min, 20/h, lots de 5) | **repris, simplifiés** | haute qualité seulement, 2 h, 4/jour, une notification groupée par passage |
| Flux SSE du fil | **abandonné** | la recherche rend en 77 ms ; l'app relit le fil |
| Génération de requêtes de collecte (mots-clés × villes) | **refaite** | deux intitulés par personne, pesés par la demande, dus tout de suite si la recherche est maigre |
| `job_watch` (veille personnelle de Marc) | **hors périmètre** | outil personnel, laissé en V1 |

## Conséquences

- Trois processus, une image : l'API, le worker (`scripts/worker.py`), le
  collecteur (`python -m karria.collector`). Le collecteur peut tomber, être
  banni ou cassé : l'API et le fil n'en savent rien.
- Déploiement du collecteur : une application Dokploy « karria-collector »
  dans le projet Karria, même dépôt et même image que l'API, commande
  `python -m karria.collector` (boucle, un passage toutes les 10 min,
  `restart: unless-stopped`), variables `COLLECTOR__API_URL` (l'API par son
  nom sur `dokploy-network`), `COLLECTOR__TOKEN` (le même que
  `COLLECTOR__TOKEN` de l'API), `COLLECTOR__PROXIES` facultatif. Pas de
  tâche planifiée Dokploy en plus : la boucle *est* le calendrier ; en
  variante, une tâche planifiée `python -m karria.collector --once` toutes
  les 10 minutes, jamais les deux. Le nom d'hôte interne de l'API doit
  figurer dans `SECURITY__ALLOWED_HOSTS`. Détail : `docs/DEPLOIEMENT.md`.
- `python-jobspy` (et pandas) restent dans l'image mais ne se chargent que
  dans le processus du collecteur.
- Le seuil d'alerte (0,78), les bornes de qualité et les poids de fusion
  sont posés sur une journée de données ; ils se recalibrent avec
  `scripts/offers_eval.py`, qui rejoue les 35 demandes.
- Ce qui reste ouvert : `docs/PLUS-TARD.md` (purge des offres inactives,
  liveness des annonces, analyse IA par offre, mesure de la part nouvelle
  sur une semaine).

## Alternatives écartées

- **Le collecteur écrit dans MinIO et la base** — un processus exposé avec
  les clés de tout ; et deux écrivains du même schéma.
- **Un reclasseur** (léger : faux ; juste : 6,7 s) — voir § 6. À
  reconsidérer seulement avec un GPU.
- **BM25 calculé par PostgreSQL** (`to_tsvector`, la piste de la veille) —
  un aller-retour de plus par lot et un hachage d'indices à nous ; Qdrant
  fait la même chose, à l'index et à la requête, sans code.
- **bge-m3** (dense + creux natifs) — 1,8 offre/s : près de 11 h de calcul pour
  une seule journée de 70 000 annonces nouvelles.
- **Redis + ARQ** — l'éviction silencieuse et l'empilement de la V1.
