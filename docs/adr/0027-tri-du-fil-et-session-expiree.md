# ADR-0027 — Le tri du fil (pertinence, date, distance) en base, et un jeton refusé n'est pas un visiteur

**Statut :** Proposée — 2026-10-31 (écrite par Opus, à relire par Fable, à valider par Marc)
**Complète :** ADR-0016 (moteur de recherche d'offres et fil), ADR-0025 (lecture du fil sans compte)

## Le problème

Deux demandes de Marc sur l'écran « Pour toi » :

1. « Si je filtre, il va filtrer sur toutes les offres, alors que je suis sur
   mes recherches. »
2. Un tri rapide sur l'écran : « Pertinence · Plus récentes · Plus proches ».

### La vraie cause du (1)

Le code des filtres était juste des deux côtés (`GET /job-matches/me`
applique les filtres au mode servi). La cause est l'**authentification** :

- le jeton d'accès vit **15 min** (`ACCESS_TOKEN_LIFETIME`) ; l'app ne le
  renouvelle qu'en recevant un `401` ;
- depuis l'ADR-0025, `/job-matches/me` et `/filter-options` admettent
  l'anonyme (`allow_anonymous`), et `resolve_principal_from_session` rend
  `None` pour un jeton **périmé** comme pour un jeton **absent** ;
- donc, passé 15 min, la requête suivante — typiquement celle que déclenche
  un filtre — était servie **en anonyme** : `200`, `mode: all_offers`, tout
  le corpus, sans critères ni état. Aucun `401`, donc aucun renouvellement :
  l'app restait dans cet état jusqu'à une route authentifiée.

Côté app, deux défauts aggravaient l'impression : les options de la feuille
étaient lues **sans** `mode` (donc pas celles de la vue affichée) et jamais
relues au changement de vue ; et les filtres étaient partagés entre les deux
vues.

## Décision

### 1. « Sans session » veut dire « sans jeton »

`allow_anonymous` (socle) répond `401 unauthenticated`,
`details.reason = session_expired`, quand un `Authorization: Bearer` est
présenté mais refusé. Seule l'absence d'en-tête vaut visiteur. L'app (toutes
versions) renouvelle sur tout `401` et rejoue : le fil de recherche revient,
sans mise à jour de l'app. Vaut aussi pour la galerie de modèles (même garde).

### 2. Le tri se fait en base, dans les deux modes

`sort` : `relevance` (défaut), `fresh`, `nearest`.

- `relevance` sans recherche est servi `fresh` (il n'y a pas de score) ; la
  page rend l'ordre **servi** (`sort`).
- `nearest` : depuis le **lieu géocodé des critères**
  (`SearchCriteria.reference_point` : `location.latitude/longitude`, posés
  par le géocodage BAN à l'enregistrement de la recherche — rien à géocoder
  à la lecture, `offers_geocode_cache` n'intervient pas). Distance
  **haversine en SQL pur** (`distance_sql`), sans PostGIS : l'extension
  existe en production mais pas dans la base de test ni forcément ailleurs,
  et la trigonométrie suffit à trier. Ordre : distance croissante, `NULL`
  (offre sans coordonnées, ~1,4 % du corpus) en dernier, puis
  `published_at` décroissant, puis l'identifiant — la pagination est stable.
- `nearest` vaut aussi en « Toutes les offres » : le lieu de la recherche
  reste la référence la plus naturelle (« les offres près de chez moi »).
- Pas de point (pas de recherche, « Toute la France », télétravail seul,
  départements sans centre, invité) : `422 invalid_input`,
  `reason = nearest_unavailable`, `field = sort`. La page dit d'avance si
  c'est possible (`nearest_available`), l'app cache alors le choix.
- Chaque offre du fil porte `distance_km` (au dixième) dès qu'un point
  existe — calculée en Python sur la page, pas en SQL.
- `saved_only` reste **transversal** (une offre gardée ne se perd pas d'une
  recherche à l'autre — garantie de la V1) mais applique désormais les
  autres filtres (contrat, ville, période) et le tri, en mémoire (la liste
  est courte).

### Coût mesuré

`nearest` en « Toutes les offres » calcule la distance de toutes les offres
actives filtrées : **~125 ms** sur 307 918 offres en production (2026-10-31,
`LIMIT 20`, sans index). En recherche, quelques centaines de lignes : négligeable.
Si le corpus décuple : boîte englobante indexée (`latitude`, `longitude`) ou
PostGIS + GiST, sans changer le contrat.

## Conséquences

- Contrat app : `karria_app/docs/contrat-api-offres.md` (« Le tri », « Session
  expirée »).
- Un client qui garde un jeton mort en croyant être invité reçoit désormais un
  `401` au lieu du corpus : c'est voulu ; l'app efface la session si le
  renouvellement échoue, et repasse alors en invité.
- Aucune migration.
