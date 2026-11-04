# ADR-0028 — Suggestions à la saisie du CV : vocabulaire matérialisé depuis l'index d'offres, listes de référence embarquées

**Statut :** Proposée — 2026-11-04 (écrite par Fable, à valider par Marc)
**Complète :** ADR-0014 (persistance du CV), ADR-0016 (moteur d'offres)

## Le problème

Quand on tape un intitulé de poste, une entreprise, une ville, une école, un
diplôme, une compétence ou une langue dans l'éditeur de CV, « comme dans
toutes les grandes apps », on attend des propositions. L'API possède déjà un
corpus de 54 000 offres portant intitulés, employeurs et villes ; elle ne
possède ni écoles, ni diplômes, ni compétences.

Deux contraintes : une réponse en **moins de 50 ms** à chaque frappe, et la
règle 4 — le module `cv` ne lit pas les tables d'`offers`.

## La décision

1. **Une route de session**, `GET /api/v1/cv/suggestions?kind=…&q=…`, qui
   rend huit libellés au plus, par préfixe du libellé **ou d'un de ses
   mots**, insensible à la casse et aux accents (`fold`). Session requise,
   comme tout le module : la saisie libre reste possible, la route n'est
   qu'une aide, et elle n'a de sens qu'en éditant son CV. Aucune limite de
   débit propre : les routes de session n'en portent pas ici.
2. **Trois familles matérialisées** (`title`, `company`, `city`) dans une
   table du module `cv` (`cv_suggestions` : `kind`, `label`, `folded`,
   `weight`), **remplacée chaque nuit** par le worker (`cv.refresh_suggestions`,
   4 h 20) — jamais un `DISTINCT` sur les offres à chaque frappe. La
   recherche est un `LIKE 'q%'` servi par un index `varchar_pattern_ops`,
   plus un `LIKE '% q%'` pour les débuts de mot, sur quelques dizaines de
   milliers de lignes.
3. **Quatre familles embarquées** (`school`, `degree`, `skill`,
   `language`) dans `modules/cv/domain/references/*.txt` — une valeur par
   ligne, en français, l'ordre faisant le poids — lues une fois par
   processus. Elles se modifient par une livraison, pas par une table.
4. **Le vocabulaire d'`offers` arrive par un port** (`OfferVocabulary`,
   des paires brutes libellé/occurrences) que `bootstrap/worker.py` branche
   sur `offers.build_offer_vocabulary`. Le nettoyage (« H/F », séparateurs,
   majuscules, doublons à l'accent près, seuil d'occurrences) est une règle
   du domaine `cv` : l'index n'a pas à connaître ce qu'un CV porte.

## Ce qu'on a écarté

- **`pg_trgm` / `unaccent`** : deux extensions à installer sur la base de
  production pour un gain qu'on n'a pas mesuré ; le pliage en Python et
  l'index de préfixe suffisent à la taille du vocabulaire.
- **Une vue matérialisée** : elle vivrait dans le schéma d'`offers` et
  obligerait `cv` à la lire, ou `offers` à servir une route pour `cv`. Une
  table possédée par `cv`, remplie par son propre travail de file, garde
  la frontière nette.
- **Chercher au milieu des mots** (« table » → « Comptable ») : trop de
  bruit pour une saisie de deux lettres ; le début de mot couvre « rayon »
  → « Chef de rayon ».

## Conséquences

- Migration 15 (`cv_suggestions`), à appliquer avant le code ; première
  matérialisation par `python -m scripts.suggestions refresh`.
- Les suggestions `title|company|city` sont **vides** tant que le worker
  n'a pas tourné une fois : dégradé, pas cassé.
- Les listes embarquées sont versionnées avec le code : une école qui
  manque est une ligne à ajouter, revue comme du code.
