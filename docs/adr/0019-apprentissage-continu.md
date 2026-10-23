# ADR-0019 — Apprentissage continu : journal des gestes, classement appris, porte de promotion

**Statut :** Proposée — 2026-10-23 (à valider par Marc ; dépendances `scikit-learn`/`lightgbm` en attente de son accord)
**Complète :** ADR-0016 (moteur d'offres), ADR-0017 (planificateur), ADR-0018 § 6 (enrichissement gratuit d'abord, classifieurs sur plongements)
**Contexte :** module `offers` — `domain/learning.py`, `offers_interaction_events`, migration 7

## Le problème

La précision du fil tient aujourd'hui à des règles écrites à la main : porte
d'intitulé, score `0,6 × intitulé + 0,4 × rang de rappel`, filtres durs. Elles
sont bonnes (précision@10 jugée 91 %, ADR-0017) mais **n'apprennent rien** de
ce que les personnes font : une offre ouverte, gardée, candidatée ou écartée ne
change ni le classement du lendemain ni les synonymes de la porte. Marc veut
que la précision continue de monter toute seule, et savoir **quels modèles
valent le coup, avec quel signal, quelle porte de promotion, et à partir de
combien de données**.

## Ce qu'on décide

### 1. D'abord le journal — des données propres avant tout modèle

Chaque geste sur une offre est consigné **avec sa place et son contexte**
(`offers_interaction_events`, en ajout seulement) :

| geste (`kind`) | d'où | place |
|---|---|---|
| `impression` | `POST /job-matches/me/seen` (`impressions[{offer_id, position}]`) | `offset + index` de la carte telle qu'affichée |
| `open`, `link_click`, `save`/`unsave`, `apply`/`unapply`, `dismiss`/`undismiss` | `PATCH /job-matches/{id}/interaction` (bascules d'état, `position` facultative) | la place au moment du geste |
| `dwell` | le même `PATCH` avec `dwell_ms` quand la fiche se ferme | — |

L'**instantané** (`Snapshot`) fige ce qui expliquait le geste au moment où il
a eu lieu — relire l'offre plus tard ne le donnerait plus : mode du fil,
empreinte des critères, score et palier de recherche, origine (`search`/
`alert`), source, âge en heures, distance au point cherché, sorte d'accord
d'intitulé (0 : l'intitulé dit, 1 : un synonyme, `None`), accord de contrat
et de temps, salaire affiché. Rien de plus nominatif que l'identifiant de la
personne, que l'état de l'offre porte déjà ; la suppression de compte efface
tout (`ForgetUser`, à étendre au journal — voir Conséquences).

Un geste **sans** journal reste possible (`log=None`) : le journal ne bloque
jamais une interaction, et il n'est jamais lu sur le chemin d'une requête.

### 2. Les modèles, dans l'ordre où ils deviennent rentables

| # | modèle | signal d'apprentissage | traits | porte de promotion | données avant d'être utile |
|---|---|---|---|---|---|
| A | **Temps de travail**, **télétravail** (ADR-0018 § 6) | étiquettes France Travail (structurées) ; corrections des personnes plus tard | plongement e5 **sans traits** (`t3`) + tf-idf du titre | exactitude et macro-F1 sur 20 % tenus à l'écart **>** modèle courant ; ECE ≤ 0,05 ; couverture à ≥ 0,8 non dégradée | déjà là (54 000 offres, 43 000 FT étiquetées) |
| B | **Contrat** (regex → classifieur → modèle de langage) | idem | idem | idem ; le résidu sous 0,8 part au modèle de langage, jamais une prédiction faible seule | déjà là ; `stage` reste impossible (absent de FT) |
| C | **Classement appris** (learning-to-rank) | journal : `open`/`save`/`apply` positifs, `dismiss` négatif, `impression` sans geste = négatif faible, **pondéré par la place** (biais de position) | score de rappel, accord d'intitulé, distance, âge, accord contrat/temps, source, salaire affiché, historique de la personne (taux d'ouverture par source, par contrat) | hors ligne : NDCG@10 par requête **>** classement courant sur un jeu tenu à l'écart par personne ; en ligne : interleaving (ou A/B) sur ≥ 2 semaines, taux d'ouverture et de sauvegarde non inférieurs | ≥ 5 000 impressions **et** ≥ 500 gestes positifs répartis sur ≥ 50 personnes ; en dessous, LambdaMART sur-apprend : commencer par une régression logistique point à point |
| D | **Pertinence requête → intitulé** (synonymes et voisins exclus appris) | paires (intitulés demandés, titre d'offre) : `open`/`save` = pertinent, `dismiss` = non ; les élargissements acceptés (ADR-0018, `widenings`) = synonyme confirmé | tokens racinisés des deux côtés, cosinus e5, mots de niveau | précision de la porte d'intitulé sur le jeu jugé (ADR-0017, 91,4 %) **non dégradée** et rappel en hausse | ≥ 200 paires étiquetées par métier fréquent ; avant : la liste `LEVEL_WORDS` et les `excluded_titles` du modèle de langage restent la règle |
| E | **Qualité d'offre / spam / doublon** | doublons : `simhash` + bandes déjà en place (étiquettes gratuites) ; spam : `dismiss` massif d'une même annonce, signalements futurs | longueur, ratio majuscules, liens, répétition d'employeur, âge, taux d'écart par impression | précision ≥ 0,95 sur un échantillon relu à la main (on ne cache pas une vraie offre) | ≥ 300 exemples relus ; d'ici là, règles seulement |
| F | **Vecteur de préférence par personne** | `save`/`apply` (+), `dismiss` (−) sur les plongements e5 des offres | moyenne pondérée des vecteurs, décroissance temporelle | hors ligne : rappel des `save` futurs ; en ligne : comme C | ≥ 20 gestes positifs par personne ; sinon, pas de vecteur (le fil reste celui des critères) |

**Ré-entraînement planifié** (A, B, puis C) : chaque nuit, un travail de file
`offers.retrain` (après `reconcile`) apprend sur les étiquettes du jour, évalue
sur le jeu tenu à l'écart **figé par empreinte** (les mêmes offres d'une nuit
à l'autre tant qu'elles sont actives, renouvelé par tranche), et **ne promeut
que s'il bat le modèle courant** sur la porte du tableau. Les modèles sont
versionnés (`<modèle>@<AAAAMMJJ>@<empreinte des données>`) et rangés dans le
bucket avec leurs métriques ; la table `offers_model_versions` garde pour
chaque version : métriques, taille du jeu, promu ou non, et la **dérive**
(distribution des prédictions et des confiances sur les offres du jour,
comparée à la version promue — une dérive au-delà de deux écarts-types
déclenche une alerte, pas une promotion).

### 3. Ce qui change dans le vecteur dense

`embedding_text()` n'écrit plus la ligne des traits (contrat, télétravail,
temps, expérience) : elle faisait relire l'étiquette à tout classifieur appris
sur le vecteur indexé (ADR-0018 § 6, 92 à 95 % trompeurs). Version `t2 → t3` :
`reconcile` réécrit les 54 000 points. Le filtre dur applique déjà ces traits
exactement ; la branche dense perd un signal redondant, les branches lexicales
ne changent pas. À mesurer : la précision@10 jugée après ré-indexage (ADR-0017
§ Mesures) — différée (consigne de sobriété de Marc), à faire avant de
promouvoir C.

## Ce qui est livré avec cette ADR

- `domain/learning.py` (`InteractionKind`, `Snapshot`, `InteractionEvent`,
  `snapshot`, `transitions`), le port `InteractionLog`, la table
  `offers_interaction_events` (migration 7), `TortoiseInteractionLog`.
- `MarkFeedSeen` consigne chaque affichage avec sa place ; `UpdateOfferState`
  consigne chaque bascule d'état, `position` et `dwell_ms`.
- Contrat de l'app : `impressions[]` dans `POST /job-matches/me/seen`,
  `position` et `dwell_ms` dans `PATCH /job-matches/{id}/interaction`
  (`karria_app/docs/contrat-api-offres.md`).
- `embedding_text` sans traits (`t3`).

## Ce qui attend

- **L'accord de Marc** sur `scikit-learn` (A, B, C en régression logistique,
  D, E, F) et `lightgbm` (C en LambdaMART quand le volume le permet) — sans
  lui, rien ne s'entraîne ; le journal se remplit en attendant.
- L'export d'entraînement (`scripts/learning_export.py` : journal + instantanés
  → fichiers par jour), le travail `offers.retrain`, la table des versions, la
  porte de promotion, l'alerte de dérive — à écrire une fois la dépendance
  accordée.
- La suppression du journal d'une personne à la suppression de son compte
  (`ForgetUser`) : à ajouter avec le premier modèle qui le lit.

## Conséquences

- Une ligne par geste : à 1 000 personnes actives et 200 impressions par jour
  chacune, ~200 000 lignes/jour, ~70 Mo/mois — une purge à 180 jours est à
  prévoir (`PLUS-TARD.md`).
- Le `PATCH /interaction` accepte désormais un corps qui ne change pas l'état
  (`dwell_ms` seul) : il consigne, et rend l'offre telle quelle.
- Le biais de position est **mesurable** dès maintenant (taux d'ouverture par
  `position`) : c'est la première courbe à regarder avant d'entraîner C.

## Alternatives écartées

- **Apprendre sans journaliser la place** : le taux d'ouverture confond la
  pertinence et la visibilité ; un modèle appris là-dessus apprend le
  classement courant.
- **Un seul grand modèle de bout en bout** (requête + offre → clic) : il
  faudrait des centaines de milliers de gestes ; les règles actuelles sont
  meilleures que ce qu'il apprendrait sur nos volumes pendant des mois.
- **Un modèle de langage pour classer** : trop lent et trop cher par offre et
  par personne ; il reste pour l'enrichissement du résidu (ADR-0018 § 6).
