# Classifieurs légers sur embeddings e5 — champs manquants des offres

_Expérience lecture seule, serveur papex-new, base `karria_eval`._

## ⚠️ Fuite découverte dans le vecteur indexé

`embedding_text()` (modules/offers/domain/indexing.py) insère la ligne **traits** (contrat, télétravail, temps, expérience) en clair dans le texte embeddé. Un classifieur sur le vecteur Qdrant *relit* donc l'étiquette : la v1 (toute la base, vecteurs indexés) est **optimiste et non représentative** d'une offre dont le champ manque (archivée dans `v1_leaky/`).

La v2 ré-embedde un échantillon **sans** la ligne traits via le TEI local (e5-base int8, 19.39 textes/s, 1032 s) et compare propre vs indexé sur le même échantillon. Cos moyen propre/indexé : 0.953.

Échantillon : 20000 offres (14000 FT, 6000 Indeed/HelloWork), tirées au hasard (seed 42) parmi les offres actives canoniques. 80/20 stratifié, seed 42, FT étiquetées uniquement. LogisticRegression(lbfgs, C=4, max_iter=3000), sans pondération de classes. Titres : TfidfVectorizer(1-2 grammes, min_df=3, sublinear_tf) ajusté sur les titres FT d'entraînement.

## 1. Précision sur FT tenue à l'écart (20 %)

| Champ | Variante | Modèle | Accuracy | Macro-F1 | Baseline majoritaire | ECE |
|---|---|---|---|---|---|---|
| schedule | clean | emb | 87.4 % | 0.739 | 81.8 % | 0.018 |
| schedule | clean | emb+title ★ | 88.1 % | 0.770 | 81.8 % | 0.026 |
| schedule | indexed_leaky | emb | 92.5 % | 0.854 | 81.8 % | 0.043 |
| schedule | indexed_leaky | emb+title | 91.8 % | 0.843 | 81.8 % | 0.016 |
| schedule | v1 base entière (fuite) | emb | 94.6 % | 0.899 | 82.2 % | 0.045 |
| schedule | v1 base entière (fuite) | emb+title | 94.7 % | 0.901 | 82.2 % | 0.030 |
| contract_type | clean | emb | 74.5 % | 0.477 | 52.4 % | 0.045 |
| contract_type | clean | emb+title ★ | 76.2 % | 0.669 | 52.4 % | 0.014 |
| contract_type | indexed_leaky | emb | 94.3 % | 0.813 | 52.4 % | 0.072 |
| contract_type | indexed_leaky | emb+title | 93.8 % | 0.823 | 52.4 % | 0.054 |
| contract_type | v1 base entière (fuite) | emb | 95.2 % | 0.861 | 52.2 % | 0.050 |
| contract_type | v1 base entière (fuite) | emb+title | 95.6 % | 0.906 | 52.2 % | 0.037 |
| experience | clean | emb ★ | 62.7 % | 0.411 | 51.7 % | 0.031 |
| experience | clean | emb+title | 58.4 % | 0.393 | 51.7 % | 0.073 |
| experience | indexed_leaky | emb | 92.9 % | 0.688 | 51.7 % | 0.117 |
| experience | indexed_leaky | emb+title | 91.1 % | 0.706 | 51.7 % | 0.091 |
| experience | v1 base entière (fuite) | emb | 93.6 % | 0.733 | 51.7 % | 0.066 |
| experience | v1 base entière (fuite) | emb+title | 94.4 % | 0.773 | 51.7 % | 0.066 |
| remote | clean | emb | 94.5 % | 0.522 | 94.4 % | 0.014 |
| remote | clean | emb+title ★ | 95.0 % | 0.665 | 94.4 % | 0.005 |
| remote | indexed_leaky | emb | 96.5 % | 0.763 | 94.4 % | 0.021 |
| remote | indexed_leaky | emb+title | 96.8 % | 0.796 | 94.4 % | 0.009 |
| remote | v1 base entière (fuite) | emb | 97.8 % | 0.871 | 94.6 % | 0.018 |
| remote | v1 base entière (fuite) | emb+title | 97.8 % | 0.870 | 94.6 % | 0.012 |

Expérience (propre) — erreur moyenne en niveaux / à ±1 niveau : emb: 0.48 / 90.0 %, emb+title: 0.52 / 89.8 %

### Par classe (propre, meilleur modèle)

| Champ | Classe | Précision | Rappel | F1 | Support |
|---|---|---|---|---|---|
| schedule | temps_partiel | 0.75 | 0.51 | 0.61 | 261 |
| schedule | temps_plein | 0.90 | 0.96 | 0.93 | 1175 |
| contract_type | alternance | 0.93 | 0.54 | 0.68 | 48 |
| contract_type | cdd | 0.65 | 0.41 | 0.50 | 303 |
| contract_type | cdi | 0.77 | 0.84 | 0.80 | 1467 |
| contract_type | interim | 0.77 | 0.78 | 0.77 | 955 |
| contract_type | other | 0.80 | 0.46 | 0.59 | 26 |
| experience | confirme | 0.50 | 0.36 | 0.42 | 482 |
| experience | debutant | 0.67 | 0.85 | 0.75 | 1426 |
| experience | junior | 0.56 | 0.41 | 0.47 | 825 |
| experience | senior | 0.00 | 0.00 | 0.00 | 25 |
| remote | aucune_mention | 0.96 | 0.99 | 0.97 | 2643 |
| remote | teletravail_mentionne | 0.63 | 0.25 | 0.36 | 157 |

## 2. Seuils de confiance (propre, meilleur modèle, FT test)

Couverture = part des offres auto-remplies ; résidu = part à envoyer au LLM.

| Champ | ≥0.6 couv. / acc. | ≥0.7 | ≥0.8 | ≥0.9 |
|---|---|---|---|---|
| schedule | 94.4 % / 90.3 % | 87.5 % / 91.6 % | 79.9 % / 92.7 % | 64.4 % / 95.5 % |
| contract_type | 79.7 % / 82.6 % | 66.9 % / 85.8 % | 51.2 % / 89.8 % | 31.0 % / 93.3 % |
| experience | 44.1 % / 77.9 % | 25.7 % / 82.5 % | 10.8 % / 90.9 % | 2.0 % / 96.3 % |
| remote | 98.6 % / 95.7 % | 96.3 % / 96.3 % | 93.2 % / 97.1 % | 87.5 % / 98.1 % |

## 3. Indeed + HelloWork (étiquettes regex = proxy bruité)

| Champ | n étiquetées | Accord global | Accord classes apprises | Couverture ≥0.8 | Accord ≥0.8 | Indeed | HelloWork |
|---|---|---|---|---|---|---|---|
| schedule | 4993 | 84.5 % | 84.5 % | 72.7 % | 91.4 % | 82.0 % | 86.6 % |
| contract_type | 5196 | 57.0 % | 63.3 % | 49.4 % | 70.4 % | 54.3 % | 59.5 % |
| experience | 2659 | 41.9 % | 41.9 % | 2.1 % | 78.9 % | 38.1 % | 45.7 % |
| remote | 6000 | 88.2 % | 88.2 % | 85.9 % | 92.1 % | 86.3 % | 90.4 % |

Principales confusions (regex → modèle) :

- **schedule** : temps_partiel→temps_plein (540), temps_plein→temps_partiel (233)
- **contract_type** : cdd→cdi (768), stage→cdi (277), interim→cdi (264), stage→cdd (216), alternance→cdi (184)
- **experience** : debutant→junior (444), confirme→debutant (294), confirme→junior (240), debutant→confirme (139), senior→confirme (139)
- **remote** : teletravail_mentionne→aucune_mention (582), aucune_mention→teletravail_mentionne (128)

Offres sans étiquette (prédiction) :

| Champ | n | Distribution prédite | Part ≥0.8 |
|---|---|---|---|
| schedule | 1007 | {'temps_plein': 947, 'temps_partiel': 60} | 82.3 % |
| contract_type | 804 | {'cdi': 692, 'cdd': 82, 'interim': 30} | 56.0 % |
| experience | 3341 | {'debutant': 2144, 'junior': 911, 'confirme': 286} | 3.4 % |

## 4. Coûts

| Champ | Modèle retenu | Entraînement (s) | Inférence / 1k offres (s, CPU) | Taille modèle |
|---|---|---|---|---|
| schedule | emb+title | 0.54 | 0.0255 | 40.5 Ko |
| contract_type | emb+title | 22.83 | 0.026 | 186.1 Ko |
| experience | emb | 17.84 | 0.0009 | 12.0 Ko |
| remote | emb+title | 0.5 | 0.0248 | 66.2 Ko |

Inférence = LR (+ tf-idf du titre) sur un embedding déjà calculé. Si l'on retire les traits du texte embeddé (voir limites), aucun embedding supplémentaire n'est nécessaire ; sinon il faut un 2ᵉ embedding « propre » (~19.39 offres/s sur ce CPU partagé). Durée totale de l'expérience v2 : 1188 s.

## 5. Limites

- **Fuite** : les vecteurs indexés contiennent les étiquettes ; seuls les chiffres « clean » valent pour une offre au champ manquant. Pour un usage réel, il faut soit retirer la ligne traits d'`embedding_text` (et ré-indexer), soit calculer un embedding dédié sans traits.
- **Étiquettes JobSpy** déduites par regex (et `job_types`), donc bruitées : l'accord n'est qu'un proxy, pas une précision.
- **Biais schedule** : FT ne renseigne le temps de travail que pour ~53 % des offres (absent surtout pour l'intérim et le non-salarié) ; le modèle est appris sur cette sous-population.
- **Contrat** : FT n'a pas de `stage` ; le modèle ne peut pas le prédire (les stages JobSpy partent en cdi/interim/cdd). `other` = freelance + saisonnier + autre.
- **Expérience** : sur FT, `debutant` vient du drapeau API et les autres niveaux des années demandées ; `senior` est très rare (<1 %).
- **Remote** : FT ne fournit pas le télétravail ; ses étiquettes sortent de la même regex que JobSpy et `sur_site` n'existe jamais. On ne mesure que « mention de télétravail vs aucune », et l'embedding ne voit que les 450 premiers caractères de la description.
- Probabilités de LR brutes (non recalibrées) ; l'ECE est donnée pour juger leur fiabilité.
