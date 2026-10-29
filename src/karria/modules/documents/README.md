# Module `documents` — modèles de CV, exports PDF et DOCX, score ATS

Décision : ADR-0020 (validée par Marc le 2026-10-24). Contrat :
`karria_app/docs/contrat-api-modeles.md`.

## Ce qu'il possède

- Le **catalogue** des modèles (`documents_templates`) et leurs **versions**
  (`documents_template_versions`) : la spécification canonique tirée du PDF,
  les mesures de fidélité, le rapport de polices, les clés des aperçus et des
  fichiers. Une version publiée ne change plus ; une nouvelle analyse crée une
  nouvelle version (`version` + 1), que l'app utilise pour invalider ses caches.
- La **bibliothèque de polices libres** (bucket `templates`, `fonts/<famille>/`),
  téléchargée une fois (Google Fonts, sinon Fontsource) et relue ensuite.
- Ce que chaque personne **possède** (`documents_user_templates`) et a **choisi**
  (`documents_selections`), et ses **exports** (`documents_generated_cvs`,
  fichiers dans le bucket `documents`).

## Ce qu'il ne possède pas

- Le CV : lu par le port `CvSource`, branché sur `cv.GetMyCv` dans
  `bootstrap/wiring.py`. Sans branchement, tout rendu répond `cv_missing`.
- Le compte (référencé par `user_id`), l'achat d'un modèle Pro.

## La chaîne

```
PDF du modèle ──► pdf_reader (pdfplumber + pypdfium2) ──► fonts (résolution)
              ──► llm.VisionLineLabeler (rôle des lignes, tâche template_extract)
              ──► spec_builder ──► spécification (JSON, version 1)
spécification + CV ──► domain/view ──► html_renderer ──► Gotenberg (Chromium) ──► PDF
                                   └─► docx_renderer (python-docx, polices incorporées) ──► DOCX
PDF / DOCX ──► ats_reader (comme un ATS) ──► domain/ats (score déterministe)
                                          └─► llm.LlmAtsExplainer (explication, en file)
```

- **Porte de qualité** (`domain/gate.py`) : PDF ≥ 0,90 et planchers par
  composante ; sinon la version reste `review`.
- **Polices** (`domain/fonts.py`, `infrastructure/fonts.py`) : incorporée si sa
  licence le permet et qu'elle couvre le français, sinon même famille libre,
  sinon remplaçante la plus proche (classe, hauteur d'x, capitales, chasse).
  Toujours des instances **statiques** ; chaque substitution est journalisée
  (`documents.font_substituted`) et rangée dans le rapport de la version.
- **Poste visé** : toujours sous le nom (`domain/headline.py`), même quand le
  modèle n'a pas d'emplacement ; omis proprement si le CV n'en a pas.

## Exploitation

- Déposer un modèle : `python -m scripts.templates submit F.pdf --name x --label X [--inline]`
  ou `POST /api/v1/internal/templates` (jeton `DOCUMENTS__ADMIN_TOKEN`).
- Revue : `python -m scripts.templates list` ou `GET /api/v1/internal/templates`.
- Travaux de file : `documents.ingest_template`, `documents.explain_ats`.
- Configuration : `DOCUMENTS__GOTENBERG_URL`, `DOCUMENTS__ADMIN_TOKEN`,
  `LLM__TASKS__TEMPLATE_EXTRACT`, `LLM__TASKS__ATS_EXPLAIN`,
  `STORAGE__PUBLIC_ENDPOINT_URL` (signature des URL pour l'hôte public).

## Relecture du 2026-10-29

- La variante « optimisée ATS » est mesurée avant d'être proposée (ADR-0020 § Mise en œuvre).
- `FontLibrary` : caches **par processus** (`_SHARED_RECORDS`, `_SHARED_FILES`) ; `file()` ne
  télécharge jamais sauf `download=True` (ingestion). Un rendu ne touche pas Internet.
- Ingestion bornée (une page, ≤ 0,25 m², images ≤ 30 Mpx) ; PDF > 20 Mio refusé en 422 ;
  `RenderingUnavailableError` n'enterre pas une version (le travail se rejoue).
- La clé des aperçus en cache porte `PREVIEW_CACHE_VERSION` : à incrémenter quand le rendu change.
