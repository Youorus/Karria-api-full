# ADR-0020 — Modèles de CV : une spécification canonique tirée du PDF, deux rendus (PDF, DOCX), un score ATS déterministe

**Statut :** Proposée — 2026-10-24 (à valider par Marc ; dépendances en attente de son accord, § 9)
**Complète :** ADR-0008 (stockage objet), ADR-0013 (sortie de modèle = donnée), ADR-0014 (CV en JSONB, éléments à `id` stable), ADR-0018 (couche LLM, routage par tâche)
**Contexte :** futur module des documents (modèles, exports) — **non créé** : son découpage attend la décision de Marc (§ 8)

## Le problème

Marc dépose le **PDF** d'un modèle de CV (exports Canva : `~/Documents/Template Karria/c1…c4`,
`~/Desktop/Modele de CV/v2/cv1…cv10`). Karria doit en tirer de quoi :

1. **montrer** le modèle (galerie, détail, aperçus — l'onglet Modèles de la V1) ;
2. **s'en servir** : rendre le CV de n'importe qui **dans ce dessin exact**, en PDF et en DOCX
   modifiable, avec les champs que le modèle n'a pas (le poste visé) ajoutés naturellement ;
3. **noter** chaque CV produit pour les ATS, avec des conseils actionnables ;
4. plus tard, **adapter** le CV à une offre (modèle + CV + offre → CV ciblé).

La fidélité au PDF n'est pas une impression : elle se **mesure** (§ 4), avec un seuil.

## Ce que fait la V1, et ce qu'on en garde

La V1 n'a **aucune** ingestion de PDF dans l'API : un outil à part
(`~/Documents/Karria_templates/template_generator`) demande à Gemini d'**écrire** un gabarit
Jinja/HTML/CSS à partir du PDF, puis le pousse par `POST /machine/submit-full` ; l'API le
rend avec Playwright. Fichiers : `app/cv_template/{generator,orchestrator,service}.py`,
`app/pdf_engine/{renderer,browser_pool,font_manager,html_builder}.py`,
`app/pdf_engine/templates/ats_optimizer.py`.

| V1 | Constat | v2 |
|---|---|---|
| Gabarit HTML/CSS **écrit par un modèle** de langage, stocké en colonnes `TEXT` (`html`, `css`) | Fidélité **0,32** au PDF source (mesurée, § 5) ; aucune version : un CV ne se re-rend pas avec la version qui l'a produit | **Spécification JSON** extraite du PDF (géométrie, polices, couleurs mesurées) ; le modèle de langage ne fait qu'**étiqueter** les lignes. Versions immuables |
| PDF source jeté après ingestion, `example_data` jeté | Impossible de ré-ingérer quand l'extracteur s'améliore | Le PDF source est **gardé** (bucket), avec ses aperçus et son contenu d'exemple |
| Playwright + pool de navigateurs dans l'API | Chromium dans le processus de l'API, versions divergentes (1.56 / 1.59) | **Gotenberg** auto-hébergé (Chromium + LibreOffice), version épinglée, mémoire bornée, hors de l'API |
| Polices variables → glyphes **Type3** dans le PDF | Un ATS lit mal ou pas du tout (mesuré sur `MERIDIAN_CV_4.pdf` : 166 polices Type3) | Instances **statiques** TrueType ; le contrôle ATS refuse le Type3 |
| `autoescape=False`, `networkidle` | Injection HTML possible depuis le texte d'une personne | Texte **échappé**, page sans aucune ressource extérieure (polices et images voyagent avec le HTML) |
| Une seule page forcée, contenu **raccourci par un modèle** de langage (`overflow_validator`) | Le modèle réécrit le CV pour qu'il tienne | Ajustement **déterministe** (espacements puis corps), seconde page assumée au-delà ; aucun mot réécrit (§ 3.4) |
| `ATSOptimizer` : bloc caché `#ats-linear` hors écran, JSON-LD, XMP | Ressemble à du bourrage de mots-clés ; **aucun score** | Aucun texte caché. **Score** mesuré sur le document tel qu'un ATS le lit (§ 6) |
| Pas d'export DOCX | — | DOCX natif depuis la **même** spécification, styles Word nommés |
| Clés S3 avec l'e-mail (`users/{id}_{email}/…`), colonnes `*_url` | Donnée personnelle dans les clés, URL en base | `build_key()`, clés opaques, URL signées à la lecture (ADR-0008) |
| `template_products`, `user_templates`, `template_reviews`, `generated_cvs` (4 modules) | Prix en double (`price` × `template_products`), routes en collision (`/templates/{id}` × documents générés) | **Un** module « documents » (à valider, § 8) ; le prix vit à un seul endroit |
| **Gardé** : catalogue avec catégorie, étiquettes, `is_premium`, `has_photo`, compteurs ; possession (`/user-templates/me`) ; aperçus en plusieurs tailles ; lettre assortie ; métadonnées PDF (titre, auteur) | Fonctionnellement juste, l'app l'affiche | Mêmes noms de champs côté contrat (`contrat-api-modeles.md`) |

## Les options mesurées

Preuve de concept sur **trois** modèles réels (Meridian = c4, Minimalist = c2, Marketing =
c1), code et résultats dans `~/Documents/Karria-modeles-poc-2026-10-24/` (hors dépôt).

- **A — spécification canonique** : extraction déterministe (polices, tailles, couleurs,
  interlettrage réel calculé sur la police, boîtes, filets, pastilles, détourages) +
  étiquetage des lignes par un modèle de vision (`shared/llm`) → `spec.json` → deux
  moteurs : HTML/CSS → PDF (Chromium de Gotenberg) et python-docx → DOCX.
- **B — DOCX d'abord** : `pdf2docx` reconstruit un DOCX à partir du PDF, puis LibreOffice
  le convertit en PDF ; il resterait à y poser des balises (`docxtpl`) par un modèle.
- **Hybride** : A pour la vérité et le PDF, DOCX dérivé de A.

| Rendu du **contenu d'exemple** comparé au PDF source | Score | SSIM tolérant | Mots à ≤ 2 mm | Décor | Polices |
|---|---|---|---|---|---|
| Témoin négatif (un autre modèle) | 0,29 | 0,58 | 0 % | 0 % | — |
| V1 (`MERIDIAN_CV_4.pdf`, rendu Playwright de son gabarit) | 0,32 | 0,60 | 0 % | 7 % | Type3 |
| **B** pdf2docx → LibreOffice — Meridian / Minimalist / Marketing | 0,42 / 0,38 / 0,44 | 0,60 / 0,70 / 0,67 | 3 % / 0 % / 0 % | 0 % | ok |
| **A** HTML → PDF — **Meridian** | **0,924** | 0,906 | 94 % | 87 % | ok |
| **A** HTML → PDF — **Minimalist** | **0,922** | 0,902 | 99 % | 79 % | ok |
| **A** HTML → PDF — Marketing | 0,705 | 0,799 | 59 % | 34 % | Aileron absente |
| **A** → DOCX (aperçu LibreOffice) — Meridian / Minimalist | 0,81 / 0,73 | 0,93 / 0,85 | 96 % / 81 % | 0 % | ok |

B ne reproduit ni la position des mots (cadres flottants, polices de repli, 2 à 4 pages
pour une seule) ni une structure réutilisable : c'est une photocopie, pas un gabarit, et il
faudrait encore un modèle de langage pour y poser des balises. **Écartée.**

Marketing échoue pour des raisons identifiées, que la porte de qualité doit justement
attraper : une police non libre et indisponible (Aileron), une erreur **dans le modèle
lui-même** (un bloc « références » sous un second titre « LANGUAGES »), des puces de
formation non reconnues.

## Ce qu'on décide

### 1. La spécification canonique est la vérité (option A, DOCX dérivé)

`spec.json` (version 1), produit une fois à l'ingestion, immuable par version :

- `page` (format, fond), `fonts` (famille, graisse, style) ;
- `styles` par rôle et par rubrique (`experiences.item_title`, `skills.skill`…) :
  famille, graisse, corps, couleur, interlettrage, capitales, interligne mesuré ;
- `header` : éléments à position absolue (nom, prénom, poste visé, coordonnées logées dans
  l'en-tête), photo (cadre réel lu dans le **détourage** du PDF, forme), ornements ;
- `bands` → `columns` → `sections` : l'en-tête puis des bandes de colonnes ; chaque rubrique
  porte un **gabarit d'élément** (emplacements ordonnés, marges mesurées, retraits,
  alignement — justifié détecté), ses puces (forme **vue**, taille, couleur, décalage),
  ses pictogrammes (rastérisés à 600 dpi depuis la source), ses séparateurs en ligne
  (« Entreprise | 2016 ») et ses ornements **ancrés** à l'élément, à la ligne qu'ils
  centrent ou à la rubrique (étirés s'ils bordent toute la hauteur) ;
- les filets entre bandes, ancrés à la bande ; le reste, fixe sur la page.

Le modèle de langage ne fournit que le **rôle** de chaque ligne (`labels.json` : prénom,
titre de rubrique, intitulé, dates, puce…) et les métadonnées de galerie ; toute la
géométrie vient du PDF. Sa sortie est une donnée validée par schéma (ADR-0013) : une ligne
non étiquetée bloque la publication. Coût mesuré : **11 à 14 s, ~6 000 jetons en entrée,
~2 000 en sortie** par modèle (`gemini-3.8-flash`), une seule fois par version.

Détails qui ne se devinent pas (tous mesurés pendant la preuve) :

- L'**interlettrage** se recalcule avec la vraie police (largeur mesurée − largeur
  naturelle) ; sous 0,3 pt c'est du bruit (crénage) et il vaut 0. Les lignes « B E N J A M I N »
  se recollent par l'écart entre glyphes, ce qui retrouve les vrais espaces de mots.
- Canva dessine des **carrés détourés en ronds** et des **remplissages à opacité nulle** :
  la forme se juge sur le rendu, l'opacité se relit dans le PDF.
- Un trait est parfois dessiné deux fois (filet + forme pleine) : on garde le filet.

### 2. Deux moteurs, un seul placement

`view` = le CV v2 (ADR-0014) projeté sur les emplacements du modèle : c'est le **seul**
endroit qui sait quel champ va où (« Poste - Entreprise » si le modèle les réunit, dates au
format du modèle, catégories de compétences, niveau de langue). Le placement des rubriques
est calculé une fois et servi aux deux moteurs :

- **PDF** : HTML/CSS généré (pas de gabarit écrit à la main), converti par **Chromium**
  (Gotenberg). Les polices partent **avec la requête** en instances statiques : une police
  variable sort en **Type3**, illisible par un ATS. Tous les blocs de texte sont
  positionnés de la même façon : sinon Chromium les peint dans deux phases et l'ordre du
  texte extrait ne suit plus l'ordre de lecture (mesuré : « Maison Ravel » lu avant
  « DATA ANALYST »).
- **DOCX** : python-docx depuis la même spécification. **Un style Word nommé par rôle**
  (« Karria · Intitulé », « Karria · Puce »…) : la personne change une police partout d'un
  geste. Colonnes = cellules d'un tableau sans bordure à largeurs fixes (grille *et*
  cellules : LibreOffice lit l'une, Word l'autre) ; interligne « exact » compensé (Word et
  LibreOffice placent l'excédent **au-dessus** du texte, Chromium le centre). Les
  ornements (frises, pastilles d'élément) n'y sont **pas** encore : étape suivante, en
  formes DrawingML ancrées au paragraphe. LibreOffice ne sert qu'à **mesurer** le DOCX.

### 3. Ce que le modèle n'a pas

1. **Poste visé** (`identity.headline`) absent du modèle : synthétisé dans sa grammaire —
   famille et couleur du nom, graisse normale, ~72 % du corps des titres de rubrique,
   capitales si les titres le sont, interlettrage **plafonné à 0,12 em** (lisible par un
   ATS par construction) ; placé sous le nom, ou à côté d'un filet d'en-tête qu'il
   chevaucherait, centré sur lui (Meridian).
2. **Rubrique absente** (Langues, Centres d'intérêt, Projets) : elle emprunte la grammaire
   d'une rubrique sœur (rôles renommés, écart usuel entre rubriques du modèle) et va dans
   la colonne **la plus courte** de sa bande, d'après une pré-mise en page estimée avec les
   métriques réelles des polices.
3. **Coordonnée sans emplacement** (site, LinkedIn) : une ligne de plus, au style de la
   dernière coordonnée.
4. **Texte trop long dans l'en-tête** : interlettrage resserré (jusqu'à 30 %), puis corps
   réduit (jusqu'à 80 %), puis retour à la ligne — décidé sans navigateur.
5. **Contenu trop long pour une page** : après chargement des polices, espacements ×1 → ×0,6
   puis corps ×1 → ×0,88 ; au-delà, une seconde page. L'état retenu (`k`, `f`, pages) part
   dans les métadonnées du PDF, et le DOCX applique **les mêmes facteurs**. Aucun mot n'est
   réécrit ni supprimé.

### 4. La fidélité se mesure, avec un seuil

Rendu du modèle **avec son propre contenu d'exemple**, comparé page à page au PDF source
(72 dpi, niveaux de gris) :

- **SSIM tolérant** : SSIM par tuiles de ~22 mm, chaque tuile au meilleur recalage à ±1 mm,
  tuiles blanches ignorées. Pourquoi pas le SSIM brut : décaler la source **d'un pixel**
  (0,35 mm) la fait tomber à 0,82, de deux pixels à 0,76 — il mesure la gigue, pas le
  dessin. Contrôles : la source contre elle-même décalée = 1,00 ; un autre modèle = 0,58.
  Le SSIM brut reste rapporté.
- **Structure** : mots appariés source ↔ rendu, part à ≤ 2 mm de leur place, décalage
  médian ; concordance des polices, tailles, couleurs.
- **Décor** : part des formes visibles de la source (filets, pastilles, cadres) retrouvées
  à ±1,5 mm — le SSIM seul les remarque à peine (le DOCX sans ornements obtenait un SSIM
  supérieur au PDF).
- **Score** = 0,45 × SSIM tolérant + 0,25 × mots à ≤ 2 mm + 0,15 × style + 0,15 × décor.

**Seuils** — PDF : score ≥ 0,90 **et** SSIM tolérant ≥ 0,88, mots à ≤ 2 mm ≥ 90 %,
style ≥ 0,95, décor ≥ 0,75. DOCX (cette étape) : score ≥ 0,80. Un modèle sous le seuil
n'est **pas publié** : il part en revue (aperçu côte à côte, mesures) — Marketing en est
l'exemple.

### 5. Ingestion d'un modèle

Une commande explicite (jamais au démarrage, AGENTS § 6.4) puis un travail de file :
PDF déposé → bucket → extraction → étiquetage (tâche `template_extract`) → spécification →
rendu de l'exemple → mesure → porte de qualité → aperçus (pleine page PNG 150 dpi, vignette
WebP) → version publiée. Le PDF source, la spécification, les étiquettes, les mesures et
les polices (instances statiques, sous-ensembles plus tard) sont gardés par version.

### 6. Le score ATS : déterministe, expliqué par le modèle

Le document produit est lu **comme un ATS** : texte dans l'ordre du flux, sans tri
géométrique (PDF) ; XML brut dans l'ordre du document (DOCX). Barème sur 100 :

| Contrôle | Points | Ce qu'on mesure |
|---|---|---|
| Couche texte | 10 | chaque bloc du CV retrouvé tel quel dans le texte extrait |
| Polices | 8 | pas de Type3, incorporées, pas de caractère illisible ; **intitulés lus lettre à lettre** (« D A T A ») pénalisés |
| Ordre de lecture | 10 | chaque rubrique lue d'un seul tenant (recherche après son titre : un intitulé peut figurer dans le profil) |
| Coordonnées | 9 | e-mail, téléphone, nom en tête **en mots entiers** (pas dans l'e-mail) |
| Mise en page | 8 | colonnes (pénalisées seulement si l'ordre casse), tableaux, photo, nombre de pages |
| Rubriques standard | 25 | Expérience, Formation, Compétences reconnues par synonymes ; Profil, Langues en plus |
| Contenu | 10 | ≥ 2 missions par expérience, résultats chiffrés, 250 à 900 mots, dates |
| Mots-clés de l'offre | 20 | sigles et outils, groupes nominaux (« tableaux de bord ») et mots fréquents de l'offre, sans verbes ni ponctuation traversée ; couverture dans le CV (sans offre : barème ramené à 100) |

Chaque contrôle rend un **constat** et un **conseil** déterministes. Le modèle de langage
(tâche `ats_explain`) n'écrit que l'explication : deux phrases et trois conseils, à partir
des contrôles fournis comme donnée, sans jamais suggérer une compétence non déclarée
(« si c'est vrai pour toi »). Mesuré : 3,1 s, ~750 jetons en entrée.

Résultats sur le CV d'essai (fictif, data analyst, offre « Data Analyst — Lyon ») :
Meridian PDF **86**, DOCX 92 ; Minimalist PDF **91**, DOCX 93. Le PDF perd des points
sur l'espacement du modèle (nom de Meridian lu « C A M I L L E »).

**Variante « optimisée ATS »** (interlettrage plafonné à 0,12 em), mesurée : Meridian
ATS 86 → **93** pour une fidélité 0,924 → 0,908 ; Minimalist 91 → **94** pour 0,922 → 0,884.
Décision : la fidélité par défaut, la variante proposée par le conseil du score, au choix
de la personne (`ats_safe`).

### 7. L'adaptation à une offre (conçue, pas construite)

modèle + CV + offre → **vue ciblée** → mêmes moteurs. Le modèle de langage (tâche
`cv_tailor`) propose une **modification de la vue**, jamais un nouveau CV : réordonner les
missions et compétences, reformuler une mission **existante** (référencée par son `id`,
ADR-0014), choisir le poste visé parmi les intitulés de l'offre. Validation déterministe :
toute puce produite renvoie à une puce source, aucun chiffre ni outil absent de la source ;
la couverture des mots-clés avant/après est mesurée par le § 6. Un export ciblé garde
l'offre et la version du modèle (`offer_id`) ; le contrat le **réserve** déjà.

### 8. Le module (décision de Marc)

Proposition : **un** module `documents` (catalogue de modèles et leurs versions,
possession, choix de la personne, exports, score ATS), au lieu des huit de la V1
(AGENTS § 6.5). Il lit le CV par un port synchrone vers `cv`, branché dans
`bootstrap/wiring.py`. **Rien n'est créé** tant que Marc n'a pas tranché (AGENTS § 9).

### 9. Rendu auto-hébergé et dépendances

- **Gotenberg 8.38.1** (Chromium + LibreOffice), conteneur dans la composition Dokploy
  `karria-donnees` (projet Karria), image épinglée par version, `mem_limit: 1g`, réseau
  interne uniquement. Mesuré : ~530 Mio après usage, **0,4 à 1,2 s par PDF**, 0,2 à 0,8 s
  par DOCX → PDF. Les polices du rendu PDF voyagent avec chaque requête (pas d'image
  sur mesure) ; seule la mesure du DOCX a besoin des polices installées (image
  `FROM gotenberg/gotenberg:8.38.1` + polices, utilisée par l'ingestion seulement).
- Dépendances Python **proposées** (aucune n'est ajoutée, AGENTS § 6.11) :

| Paquet | Licence | Pour | Alternative écartée |
|---|---|---|---|
| `python-docx` | MIT | le DOCX | `docxtpl` (inutile : pas de gabarit écrit à la main) |
| `pdfplumber` (→ `pdfminer.six`, `pypdfium2`) | MIT / Apache-BSD | extraction à l'ingestion, lecture ATS du PDF (pdfminer est ce que lisent beaucoup d'ATS), rastérisation de la mesure | **PyMuPDF** (utilisé dans la preuve par commodité) : **AGPL-3.0**, incompatible avec un service fermé sans licence commerciale |
| `fonttools` | MIT | instances statiques des polices variables (ingestion) | aucune en stdlib |
| `numpy` | BSD | SSIM de la porte de qualité (ingestion seulement, groupe à part possible) | SSIM en Python pur : 50 × plus lent |

Pillow est déjà là (ADR-0009). Ni `pdf2docx`, ni Playwright, ni WeasyPrint.

## Conséquences

- Routage LLM : deux tâches nouvelles, `template_extract` (vision) et `ats_explain` ;
  `cv_tailor` plus tard. Gemini par défaut ; Anthropic dès que
  `LLM__PROVIDERS__ANTHROPIC__WORKSPACE_ID` est posé (images acceptées par les trois
  fournisseurs). Une route par variable `LLM__TASKS__*`, comme les autres.
- Contrat de l'app : `karria_app/docs/contrat-api-modeles.md` (galerie, détail, aperçus,
  choix, exports, score ATS) ; les noms de la V1 sont gardés là où l'app les lit.
- Ce qui reste ouvert : ornements DOCX en DrawingML ; polices non libres (Aileron) —
  refus, substitution déclarée ou licence ; sous-ensembles de polices ; seconde page
  à deux colonnes (rompre proprement une colonne) ; lettre de motivation assortie (même
  chaîne, un autre jeu de rôles) ; affichage des mots-clés manquants sous leur forme
  d'origine (aujourd'hui sans accents).

## Alternatives écartées

- **Un gabarit HTML écrit par un modèle de langage** (V1) : 0,32 de fidélité, non
  reproductible, non versionné, et une surface d'injection.
- **B, DOCX d'abord** : mesurée (0,38 à 0,44), une photocopie sans structure.
- **Positions absolues partout** : fidèle sur l'exemple, faux dès qu'un intitulé passe sur
  deux lignes ; la spécification garde les mesures **relatives** (marges, retraits) et ne
  fixe que l'en-tête.
- **Un texte caché pour les ATS** (V1) : assimilable à du bourrage ; on corrige le
  document visible (polices statiques, ordre de peinture, variante optimisée).
