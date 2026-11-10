# Module `documents` — modèles de CV, exports PDF et DOCX, score ATS, atelier, lettres, envoi

Décisions : ADR-0020 (validée par Marc le 2026-10-24), ADR-0029 (rendu HTML ancré),
ADR-0030 (lettre), ADR-0031 (envoi par e-mail), ADR-0036 (lettre appariée ou lettre
sobre), ADR-0037 (ingestion v2 : le plan de mise en page dessiné par le modèle de vision).
Contrats : `karria_app/docs/contrat-api-modeles.md` et `docs/contrat-api-documents.md`.

## Ce qu'il possède

- Le **catalogue** des modèles (`documents_templates`) et leurs **versions**
  (`documents_template_versions`) : la spécification canonique tirée du PDF,
  les mesures de fidélité, le rapport de polices, les clés des aperçus et des
  fichiers — et, quand le modèle en a une, sa **lettre appariée** (`letter_*` :
  PDF source, spécification de lettre, contenu d'exemple, rôles, aperçus),
  mesurée par la même porte. Une version publiée ne change plus ; une nouvelle
  analyse crée une nouvelle version (`version` + 1), que l'app utilise pour
  invalider ses caches.
- La **lettre sobre** (`domain/plain_letter.py`) : la spécification de lettre
  intégrée (A4, Carlito 11 pt, lettre d'affaires française), servie à tout modèle
  sans lettre appariée, et ses aperçus de galerie (`plain-letter/v<n>/` du bucket
  `templates`, déposés par `scripts.templates plain-letter`).
- La **bibliothèque de polices libres** (bucket `templates`, `fonts/<famille>/`),
  téléchargée une fois (Google Fonts, sinon Fontsource) et relue ensuite.
- Ce que chaque personne **possède** (`documents_user_templates`) et a **choisi**
  (`documents_selections`), et ses **exports** (`documents_generated_cvs`,
  fichiers dans le bucket `documents`) — des CV **et des lettres** (`kind`),
  rattachés ou non à un dossier de candidature (`dossier_id`, UUID libre).
- Le **journal des envois par e-mail** (`documents_sends`) : destinataire, documents,
  état ; c'est sur lui que se compte le quota de cinq envois par jour.
- La **lettre de motivation** comme donnée structurée (`domain/letter.py`) et ses
  règles d'usage (une page, 3–4 paragraphes, objet explicite, politesse sobre) ;
  le **style servi** (`template` | `plain`, `letter_style_served`) — le dossier
  (`applications`) retient le choix, `documents` le résout.
- Les **chemins** `data-chemin` de la vue (`view["paths"]`, `section["paths"]`), à
  côté des éléments, jamais dedans.

## Ce qu'il ne possède pas

- Le CV : lu par le port `CvSource`, branché sur `cv.GetMyCv` dans
  `bootstrap/wiring.py`. Sans branchement, tout rendu répond `cv_missing`.
- Le compte (référencé par `user_id`), l'achat d'un modèle Pro. L'adresse du
  compte, pour « m'envoyer mes documents », vient du port `AccountDirectory`
  branché sur `identity` ; sans branchement, `account_email_unknown`.
- Le dossier de candidature (module `applications`) : `documents` ne connaît que
  son identifiant. L'autre sens passe par la façade `build_render_document` /
  `get_render_document` (ADR-0029 § 6), branchée dans `bootstrap`.
- Le transport des e-mails : `shared/mail` (gabarit `documents_sent`, pièces jointes).

## La chaîne

```
PDF du modèle ──► analyzer.prepare : pdf_reader (pdfplumber + pypdfium2), fonts (résolution par famille)
              ──► layout_planner.VisionLayoutPlanner (le PLAN : en-tête, bandes, colonnes, rubriques,
                  éléments et rôles, colonne latérale, ancrage des décorations, fiche de galerie ;
                  tâche template_extract)
              ──► spec_assembler (plan + mesures ; spec_builder reste la couche de mesure)
              ──► spécification (JSON, version 1)
              ──► ingestion_loop : rendu de l'exemple ──► mesure ──► porte ; si elle échoue, UNE passe de
                  correction (côte à côte + diagnostics ──► plan corrigé), la meilleure passe gardée
spécification + CV ──► domain/view ──► html_renderer ──► Gotenberg (Chromium) ──► PDF
                                   └─► docx_renderer (python-docx, polices incorporées) ──► DOCX
PDF / DOCX ──► ats_reader (comme un ATS) ──► domain/ats (score déterministe)
                                          └─► llm.LlmAtsExplainer (explication, en file)
spécification + CV reçu ──► domain/view (+ chemins) ──► html_renderer (ancré, URL publiques) ──► l'app (WebView)
PDF de la lettre appariée ──► pdf_reader ──► fonts ──► letter_analyzer.LetterLineLabeler (rôles, tâche template_extract)
                           ──► letter_spec_builder ──► spécification de lettre (même porte que le CV)
spécification de lettre (appariée, ou domain/plain_letter) + lettre ──► domain/letter ──► letter_renderer (HTML / DOCX) ──► Gotenberg ──► PDF
export(s) ──► SendDocuments (journal, quota) ──► file ──► DeliverDocuments ──► shared/mail (pièces jointes)
```

- **Atelier** (ADR-0029) : `RenderDocument` rend le CV **reçu** (le brouillon de l'app),
  pas celui de la personne — sauf sa photo. Polices copiées une fois dans le bucket
  public `assets` (`fonts/<fichier>`) pour une adresse stable ; pictogrammes en `data:` ;
  `pages` estimé par mesure, vérité dans `window.karriaFit`. 90 rendus / min / compte.
- **Lettre** (ADR-0030, ADR-0036) : vue `kind: "letter"`, moteurs `LetterHtmlRenderer` /
  `LetterDocxRenderer` lus dans une **spécification de lettre** (blocs d'en-tête ancrés,
  colonne de texte, blancs mesurés) : la lettre appariée du modèle, recolorée avec le CV,
  ou la lettre sobre, neutre. `letter_spec_for` résout le style ; `template` sans lettre
  appariée → `plain`. Le DOCX pose chaque ligne de base d'après la règle mesurée de
  LibreOffice (`baseline_offset`). Logo téléchargé **seulement** depuis l'hôte public du
  stockage (`infrastructure/logos.py`), converti en PNG.
- **Lettre appariée à l'ingestion** (`ingest_paired_letter`) : `--letter` / champ
  multipart `letter` ; une seule remplaçante par famille du modèle ; échecs préfixés
  « lettre : » dans `gate.failures` ; la version entière reste `review` sous le seuil.
- **Fidélité et substitutions** : `measure(…, fonts_report=…)` juge la police **servie**
  quand la substitution est déclarée (sinon toute police non libre faisait tomber
  `style` sous 0,95 par construction). Le SSIM, lui, voit toujours l'autre dessin.
- **Envoi** (ADR-0031) : `202` puis travail `documents.send_documents` ; PDF sinon DOCX ;
  signé du nom de la personne ; 5 envois par jour civil UTC, comptés sur les demandes.
  Anti-hameçonnage (revues du 2026-11-05) : objet fixe, noms NFKC réduits aux lettres
  (jamais un lien, une IP, un chiffre-point), adresse relue par `parseaddr`, 2 envois/jour
  par (compte, boîte canonique `to_key`), 10/jour tous comptes, 10/heure par origine lue
  derrière `SECURITY__TRUSTED_PROXY_HOPS`, échecs non comptés, comptage sous verrou,
  réponse générique + journal `documents.send_refused` ; sa propre adresse n'est bornée
  que par le quota du compte.

- **Porte de qualité** (`domain/gate.py`) : PDF ≥ 0,90 et planchers par
  composante ; sinon la version reste `review`. Une substitution de police **déclarée**
  (rapport de polices) n'est pas une faute de mesure : elle se juge dans la revue (ADR-0037 § 4).
- **Ingestion v2** (ADR-0037) : le modèle de vision ne pose aucun millimètre ; il dit qui est
  quoi et qui s'ancre à quoi. Une ligne qu'il ne place pas bloque la publication. Le plan est
  gardé dans `labels["plan"]`, les passes (score, jetons, latence) dans `report["passes"]`.
  Deux requêtes au plus par version. Ce que la spécification sait dire de plus qu'avant : une
  rubrique sans titre (`untitled`), une rubrique libre (`other:<slug>`, titre gardé), une frise
  à colonne latérale (`side`), un champ d'en-tête sur plusieurs lignes (`box_w_mm`, `pitch_mm`,
  `sample_lines`) ou aligné à droite (`rx_mm`), un filet de bande (`bands[i]["decorations"]`)
  ou de flux (`anchor: flow`), l'image d'exemple à son échelle (`photo.sample_image`), une
  étiquette fixe devant ou au-dessus d'un champ (`slot["label"]`), un style de titre propre à
  une rubrique (`title_style`), un paragraphe aligné à droite ou centré (`slot["align"]`),
  des éléments côte à côte (`item_columns`, `item_pitch_mm`), l'espacement des mots d'un
  justifié (`styles[k]["word_spacing_mm"]`), un poste visé qui ouvre le flux (`flow`), un
  ornement propre à l'exemple (`sample_only` : barres de niveau, QR code), un tracé rastérisé
  (`asset`), le corps propre d'un élément de l'exemple (`sizes`). Le code tranche là où le
  plan se trompe de côté : coordonnées d'en-tête sous une rubrique Contact titrée, pied de
  page en bande (ADR-0037 § 2, « ce que le lot a appris »).
- **Polices** (`domain/fonts.py`, `infrastructure/fonts.py`) : incorporée si sa
  licence le permet et qu'elle couvre le français, sinon même famille libre,
  sinon remplaçante la plus proche (classe, hauteur d'x, capitales, chasse).
  Toujours des instances **statiques** ; chaque substitution est journalisée
  (`documents.font_substituted`) et rangée dans le rapport de la version.
- **Poste visé** : toujours sous le nom (`domain/headline.py`), même quand le
  modèle n'a pas d'emplacement ; omis proprement si le CV n'en a pas.
- **Couleur** (`domain/palette.py`, ADR-0034 § 2026-11-06) : la couleur d'un modèle est
  celle de son **décor**, mesurée par surface (`primary_color_of` — bandes, filets, puces
  dessinées, séparateurs ; jamais un texte). `recolor_spec(spec, primary)` ne change que le
  décor (occurrences exactes et teintes, gris par gris sur un décor gris) ; un texte posé sur
  une surface recolorée ne change que s'il ne se lit plus (4,5:1 → blanc ou noir profond,
  `legible_styles`). Les pictogrammes monochromes à la couleur du décor suivent à l'envoi
  (`application.tint_icons`, mesure et teinte par `shared.images.single_ink` / `tint_ink`,
  cache par processus). La galerie porte cette couleur (`gallery_traits`) ;
  `scripts/templates.py refresh-colors` la remesure sur les modèles déjà publiés.

## Exploitation

- Déposer un modèle : `python -m scripts.templates submit F.pdf --name x --label X [--inline] [--letter L.pdf]`
  ou `POST /api/v1/internal/templates` (jeton `DOCUMENTS__ADMIN_TOKEN`, fichiers `file` et `letter`).
- Les aperçus de la lettre sobre (galerie) : `python -m scripts.templates plain-letter`,
  une fois par environnement et à chaque changement de `PLAIN_LETTER_VERSION`.
- Revue : `python -m scripts.templates list` ou `GET /api/v1/internal/templates` ;
  `python -m scripts.templates dump VERSION_ID DIR` écrit source, rendu, côte à côte, rapport
  (passes, jetons), spécification et plan.
- Couleur de galerie : `python -m scripts.templates refresh-colors [--dry-run]` après un
  changement de sa définition ; `render-sample NOM --out DOSSIER --color '#1F4FA3'
  [--any-status]` pour voir l'exemple d'un modèle dans une couleur.
- Travaux de file : `documents.ingest_template`, `documents.explain_ats`,
  `documents.send_documents`.
- Ressources publiques : `python -m scripts.assets brand <dossier branding de l'app>`
  (mot-logos des e-mails), bucket `assets` créé par `make storage-provision`.
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
