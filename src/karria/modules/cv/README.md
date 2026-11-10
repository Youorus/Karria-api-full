# Module `cv` — le CV créé en conversation

Le contrat est `karria_app/docs/contrat-api-cv.md` ; ce module le sert sous
`/api/v1` : `GET /cv/me`, `PUT /cv/me/chapters/{chapter}`, `GET|POST /cv/draft`,
`POST /cv/draft/interpret`, `PUT /cv/draft/chapters/{chapter}`,
`POST /cv/draft/finalize`, `POST /cv/photo`, `POST /cv/me/photo/enhance`,
`POST /cv/import`, `POST /voice/transcribe`, `GET /cv/suggestions`. Toutes
exigent la session.

Trois façons d'avoir un CV, une seule forme : **l'import** d'un CV existant
(la voie principale depuis le 2026-11-04 — il écrit le CV directement, à
vérifier ensuite), **la conversation** chapitre par chapitre (pour qui n'a
pas de CV), et **l'édition directe** d'une section depuis l'écran du CV.

## Ce qu'il possède

- **Le brouillon** (`cv_drafts`) — un par personne, huit chapitres dans
  l'ordre du contrat, chacun `pending | in_progress | validated | skipped`.
  Le chapitre courant se déduit, il ne se stocke pas. Il ne sert qu'à la
  conversation ; l'import ne le remplit plus (il le supprime, puisque le CV
  existe alors).
- **Le CV** (`cv_curricula`) — un par personne, les sections de la V1 en
  JSONB (`experiences`, `educations`, `skills`, `languages`, `interests`,
  `projects`), plus `identity` et `contact`, `summary`, et `image_key`
  (servi en `image_url` signée). Chaque élément porte un `id` stable.
- **Les photos en attente** (`cv_photos`) — ce que `POST /cv/photo` a écrit
  dans le bucket `media`, originale ou améliorée, jusqu'à ce que la
  finalisation en retienne une et oublie les autres.
- **Les appels à Gemini** qui concernent le CV : interprétation d'une
  réponse, transcription d'une voix, extraction d'un CV existant, portrait.
  Quatre ports (`application/ports.py`), quatre adaptateurs, un client REST
  (`infrastructure/gemini.py`). Les prompts sont dans
  `infrastructure/prompts.py` et nulle part ailleurs.
- **Les règles sur les fichiers reçus** (`domain/uploads.py`) : types,
  10 Mo et 10 pages pour un CV, 10 Mo et 5 minutes pour un enregistrement.
- **Les suggestions à la saisie** (ADR-0028) : la table `cv_suggestions`
  (intitulés, entreprises, villes — matérialisés chaque nuit depuis l'index
  d'offres par `cv.refresh_suggestions`, ou à la demande par
  `python -m scripts.suggestions refresh`), les listes de référence
  embarquées `domain/references/{schools,degrees,skills,languages}.txt`
  (une valeur par ligne, en français, l'ordre fait le poids), et les règles
  de classement et de nettoyage (`domain/suggestions.py`).

## Ce qu'il ne possède pas, et où ça vit

- **Le compte.** `user_id` est un UUID sans clé étrangère. La finalisation
  publie `CvFinalized` (identité et contact) ; le module des comptes
  pourra s'y abonner pour mettre à jour le profil. À la **suppression** d'un
  compte, `ForgetUser` efface brouillon, CV, photos et objets — abonné à
  `AccountDeleted` par `bootstrap/wiring.py` (ADR-0015).
- **La génération de documents** (CV PDF, lettres). Elle lira le CV par
  l'API publique du module (`CurriculumVitae`, ses sections) — jamais par
  ses tables.
- **Le stockage et la normalisation des images** : `shared/storage`,
  `shared/images` (ADR-0008, ADR-0009). Le module ne manipule que des
  clés, et ne lit jamais une URL que le client lui renverrait.
- **La configuration de Gemini** : `GeminiSettings` dans `shared/config`
  (`GEMINI__API_KEY`, `GEMINI__TEXT_MODEL`, `GEMINI__IMAGE_MODEL`).
- **Les offres.** Le vocabulaire des suggestions arrive par le port
  `OfferVocabulary` (des paires libellé/occurrences), que
  `bootstrap/worker.py` branche sur `offers.build_offer_vocabulary` ; ce
  module ne lit jamais `offers_offers`.

## Les choix qu'on ne devine pas en lisant le code

- **L'import écrit le CV, pas le brouillon** (2026-11-04, retour de Marc
  sur l'appareil : revalider chapitre par chapitre ce qu'on a déjà écrit
  dans son CV est long et inutile). `POST /cv/import` remplace les
  sections du CV par ce qui a été lu — la photo reste — et rend un
  compte rendu **réel** : `kept` (retenu par section), `discarded` (lu
  mais refusé par le schéma) et `missing` (à vérifier, coordonnées
  d'abord ; `CurriculumVitae.missing_fields`). Rien n'est écrit si rien
  d'un CV n'y est lu (`422 document_not_a_cv`). Plusieurs fichiers = les
  photos des pages d'un même CV, lues ensemble (`document_pages_must_be_images`
  sinon) ; chaque photo est réduite à 2 000 px avant d'être envoyée au
  modèle.
- **L'édition directe remplace une section d'un bloc** avec le schéma du
  chapitre (`PUT /cv/me/chapters/{chapter}`, `CurriculumVitae.with_chapter`).
  `skipped` **vide** la section (sauf `identity` et `contact`), une liste
  vide est acceptée. La photo passe par `{ image_id }` de `POST /cv/photo`
  ou `{ image_id: null }` ; l'ancienne et les photos en attente sont
  oubliées après l'écriture. Sans CV : `404 cv_not_found`.
- **La photo circule par `image_id`, pas par `image_url`** (ADR-0014). Le
  contrat écrivait `{ image_url }` ; une URL signée expire et ne se
  reparse pas. `POST /cv/photo` rend les deux, le chapitre `photo` reçoit
  `{ image_id }`, et le serveur vérifie que la photo appartient bien à la
  personne.
- **La photo déjà sur le CV se retravaille sans la rechoisir**
  (`POST /cv/me/photo/enhance`, `EnhanceCurrentPhoto`, 2026-11-06) : le
  serveur relit l'objet du bucket et le confie à `UploadPhoto` avec
  `enhance` — même normalisation, même `CvPhoto` en attente. Le CV ne change
  pas : la personne compare dans l'app, puis retient l'améliorée par
  `PUT /cv/me/chapters/photo` ou garde l'originale, qui est restée.
  `409 photo_missing` sans photo ; consentement IA requis (ADR-0025).
- **La sortie du modèle est une donnée** (ADR-0013). Elle est bornée par un
  schéma JSON dérivé des modèles du domaine, puis revalidée par ces mêmes
  modèles ; ce qui n'y tient pas est retiré, journalisé
  (`cv.interpretation_rejected`) et la relance redemande. La réponse de la
  personne part entre balises, désignée comme donnée ; une injection
  d'instructions produit `off_topic: true`.
- **Pydantic vit dans `domain/`** pour les schémas de chapitre, par
  décision explicite (ADR-0013 §4) : trois frontières les emploient et ils
  portent les invariants du CV. Le contrat d'architecture reste vérifié.
- **Les PDF partent en texte** quand ils ont une couche texte (≥ 200
  caractères), tels quels sinon : constaté le 2026-10-21, Gemini lit un PDF
  joint comme une image et en perd la moitié. Le DOCX est converti en texte
  par la bibliothèque standard (`zipfile` + `word/document.xml`).
- **La réflexion des modèles Flash est coupée** (`thinking_budget: 0`) :
  trois fois plus rapide, et constaté meilleur pour l'extraction (la
  réflexion activée tronquait la sortie).
- **La durée d'un M4A se lit dans l'atome `mvhd`**, sans décoder l'audio ;
  pour les autres formats seule la taille borne.
- **Sans `GEMINI__API_KEY`, l'API démarre** et les routes qui en dépendent
  répondent `503 ai_unavailable` ; le démarrage le journalise
  (`config.gemini_disabled`) sans bloquer un déploiement.
- **L'oubli efface les objets d'abord, les lignes ensuite** (ADR-0015) —
  l'inverse de la finalisation. Une clé qui résiste garde sa ligne pour
  qu'un rejeu la retrouve ; l'échec est journalisé avec ses clés
  (`cv.forget_incomplete`). Le cas d'usage est idempotent.
- **Une suggestion est une aide, jamais une contrainte** (ADR-0028) :
  `GET /cv/suggestions?kind=title|company|city|school|degree|skill|language&q=…`
  rend huit libellés au plus, par préfixe du libellé ou d'un de ses mots,
  casse et accents indifférents ; vide pour une saisie vide, `422` pour une
  famille inconnue ou plus de 60 caractères. Les trois premières familles
  sont **vides tant que le worker n'a pas matérialisé une fois**. Les
  intitulés d'annonces sont nettoyés avant d'être proposés (« H/F »,
  séparateurs, majuscules), et un libellé vu moins de deux fois n'est pas
  « connu ».
- **Les erreurs** portent leur raison fine dans `details.reason` :
  `draft_not_found` (404), `draft_incomplete` (409, avec `chapters`),
  `chapter_invalid` / `chapter_required` / `chapter_items_expected` /
  `chapter_data_expected` / `chapter_items_empty` (422), `cv_not_found`
  (404), `document_*` (dont `document_not_a_cv`,
  `document_pages_must_be_images`) et `audio_*` (422),
  `photo_enhance_refused` (422),
  `photo_enhance_unavailable` / `ai_unavailable` / `ai_output_invalid`
  (503). Le `code` reste celui du socle (`not_found`, `rule_violation`,
  `invalid_input`, `dependency_failure`).

## Essayer en local

```bash
make up                                  # PostgreSQL + MinIO, migrations, buckets
# GEMINI__API_KEY=… dans .env (voir .env.example)
make dev
# puis, avec un jeton de session du module identity :
curl -H "Authorization: Bearer …" -X POST localhost:8000/api/v1/cv/draft
curl -H "Authorization: Bearer …" -X POST localhost:8000/api/v1/cv/draft/interpret \
     -H 'Content-Type: application/json' \
     -d '{"chapter":"identity","answer":"Je m'\''appelle Awa Diallo, je veux devenir conseillère de vente"}'
```

Les tests live contre Gemini sont ignorés par défaut :
`CV_GEMINI_LIVE=1 GEMINI__API_KEY=… uv run pytest tests/integration/test_cv_gemini_live.py`.
