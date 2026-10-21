# Module `cv` — le CV créé en conversation

Le contrat est `karria_app/docs/contrat-api-cv.md` ; ce module le sert sous
`/api/v1` : `GET /cv/me`, `GET|POST /cv/draft`, `POST /cv/draft/interpret`,
`PUT /cv/draft/chapters/{chapter}`, `POST /cv/draft/finalize`,
`POST /cv/photo`, `POST /cv/import`, `POST /voice/transcribe`. Toutes
exigent la session.

## Ce qu'il possède

- **Le brouillon** (`cv_drafts`) — un par personne, huit chapitres dans
  l'ordre du contrat, chacun `pending | in_progress | validated | skipped`.
  Le chapitre courant se déduit, il ne se stocke pas. Un chapitre importé
  est une **proposition** (`in_progress` avec son contenu) : il ne devient
  jamais `validated` sans la personne.
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

## Les choix qu'on ne devine pas en lisant le code

- **La photo circule par `image_id`, pas par `image_url`** (ADR-0014). Le
  contrat écrivait `{ image_url }` ; une URL signée expire et ne se
  reparse pas. `POST /cv/photo` rend les deux, le chapitre `photo` reçoit
  `{ image_id }`, et le serveur vérifie que la photo appartient bien à la
  personne.
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
- **Les erreurs** portent leur raison fine dans `details.reason` :
  `draft_not_found` (404), `draft_incomplete` (409, avec `chapters`),
  `chapter_invalid` / `chapter_required` / `chapter_items_expected` /
  `chapter_data_expected` / `chapter_items_empty` (422), `document_*` et
  `audio_*` (422), `photo_enhance_refused` (422),
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
