# Module `offers` — le corpus d'offres, la recherche, le fil, les alertes, le pilotage de la collecte

## Ce qu'il possède

- **Le corpus** : `offers_offers` (la vérité, dont l'index Qdrant est une
  projection reconstructible), l'ingestion par lot (dédoublonnement avant
  tout plongement, normalisation, géocodage), la boîte d'envoi vers l'index
  (`offers_index_outbox`), l'expiration et la réconciliation.
- **La recherche** : rappel hybride (dense + deux BM25) puis décision du
  domaine (`SearchCriteria.accepts`, porte d'intitulé) ; la conversation
  avec Karria ; les critères courants de chaque personne (`offers_criteria`).
- **Le fil** : `offers_feed_entries`, `offers_offer_states` (vue, ouverte,
  sauvegardée, candidatée, écartée).
- **Les alertes** : matching inversé à l'indexation (collection Qdrant
  `criteria`), notification groupée par `UserNotifier`.
- **Le pilotage de la collecte** (`domain/steering.py`) : les cibles
  canoniques `terme × département × contrat` (`offers_scrape_targets`), les
  liens personne ↔ cible (`offers_target_demands`), la priorité et la
  cadence apprises du rendement, l'essai des cibles nouvelles.
- **Les routes internes** du collecteur (`/internal/offers/targets`,
  `/internal/offers/batches`) et l'atterrissage des lots (bucket `landing`).

## Ce qu'il ne possède pas, et où ça vit

- **La collecte elle-même** : `src/karria/collector/` — un autre processus,
  qui ne connaît de ce module que son contrat HTTP (`docs/collecteur.md`).
  France Travail, JobSpy, les mandataires, la politesse envers les sites,
  les zéros silencieux : là-bas, jamais ici. Vérifié par import-linter et
  par `tests/unit/offers/test_no_live_search.py`.
- **Les appareils et l'envoi push** : module `devices`, branché par
  `bootstrap/wiring.py` sur le port `UserNotifier`.
- **Le compte** : `user_id` est un UUID opaque ; l'oubli vient de
  l'événement `AccountDeleted` (ADR-0015).
- **L'analyse de chaque offre par Karria** : n'existe pas (`PLUS-TARD.md`).

## Les choix qu'on ne devine pas en lisant le code

- **Une conversation ne cherche jamais en direct** sur une source externe :
  elle lit l'index, et au plus rend une cible de collecte due (ADR-0017 § 6).
- **Une information absente n'élimine pas**, sauf trois cas où une annonce
  dit toujours la chose quand elle est vraie : télétravail complet demandé,
  alternance ou stage demandés, **temps partiel demandé** (ADR-0017 § 4).
- **Les indices structurés d'une source** (France Travail : contrat, temps,
  expérience, salaire, coordonnées) **priment sur le texte** ; un indice hors
  des énumérations vaut « absent ».
- **La porte d'intitulé** décide de l'entrée dans un fil ; sa limite connue :
  elle accepte un titre qui contient l'intitulé (« chef cuisinier » pour
  « cuisinier »).
- **Les cibles de collecte ne portent jamais le texte d'une personne** :
  `search_terms` (1 à 4 mots, validés contre les titres réels de l'index),
  la ville du géocodeur, le contrat ; les `preferences` servent au dialogue
  et au classement, jamais à la collecte.
- **Montrer tout le corpus n'est pas abandonner la recherche** : `mode=all_offers`
  sur `GET /job-matches/me` lit le corpus actif sans toucher aux critères, qui
  continuent de nourrir le fil et les alertes. Seul `DELETE /search/criteria`
  arrête la recherche.
- Les décisions et leurs mesures : ADR-0016 (moteur), ADR-0017 (France
  Travail, collecte continue, planificateur).
