# ADR-0037 — Ingestion v2 des modèles de CV : le modèle de vision dessine la structure, le code mesure, la porte juge

**Statut :** Proposée — 2026-11-06 (à valider par Marc)
**Complète :** ADR-0020 (spécification canonique, deux moteurs, porte de fidélité), ADR-0013 et ADR-0018 (sortie de modèle = donnée, routage par tâche)
**Contexte :** module `documents`, ingestion d'un PDF de modèle (`scripts/templates.py`, travail `documents.ingest_template`)

## Le problème

Les modèles de Marc sont des exports Canva libres de droits. L'ingestion v1 (ADR-0020 § 1)
demandait au modèle de vision le **rôle** de chaque ligne, puis un constructeur déterministe
(`spec_builder.py`) devinait la structure — bandes et colonnes par l'abscisse et la hauteur des
titres de rubrique, en-tête = tout ce qui est au-dessus du premier titre. Trois modèles d'origine
passaient (0,92–0,93) ; les deux suivants tombaient à **0,51** (Sacha) et **0,52** (Romain), avec
un étiquetage pourtant juste. Les défauts, tous structurels ou de mesure :

- un paragraphe de profil **sans titre** devenait un élément d'en-tête, puis disparaissait ;
  des coordonnées sous la photo, sans titre « CONTACT », étaient perdues ;
- un poste visé écrit sur **deux lignes** interlettrées donnait deux éléments du même champ,
  rendus « PROJET PROJET » ;
- une **frise chronologique** (entreprise et dates dans une colonne étroite à gauche d'un filet,
  pastille sur le filet, contenu à droite) était lue comme trois colonnes : puces repliées sur
  49 mm, deux pages pour une ;
- les rubriques **libres** (« RÉALISATIONS CLÉS », « CERTIFICATIONS ») étaient fondues en une
  seule `other` puis omises de l'exemple ;
- les filets de séparation de bandes, le filet d'une frise, étaient ancrés au hasard ;
- une famille source substituée partait en **deux** familles servies (Inter pour le romain,
  Montserrat pour l'italique), et la fiche « Inter » rangée sous `inter` mais nommée
  « Inter 18pt » ne se retrouvait plus : tout le modèle retombait en `sans-serif` ;
- pdfium devinait la **graisse** des polices incorporées (1016 pour un italique régulier) ;
- le trait d'union d'Inter entre deux chiffres sortait sous un code privé : un ATS lisait
  « 123456 ».

Ce que Marc demande (dictée du 2026-11-06) : l'API Claude sert d'abord à créer les modèles, une
fois, de la façon la plus exploitable pour l'édition, l'export et les ATS ; il ne choisit que
Premium et son prix, le reste (catégorie, étiquettes, description, jamais les mêmes) est le
travail du modèle ; le fournisseur se change à un seul endroit ; 100 % fidèle au PDF donné ; l'IA
n'intervient que là où elle est la plus utile, à coût contenu.

## Ce qu'on décide

### 1. Le modèle de vision reçoit tout ce qui est mesuré et rend un **plan de mise en page**

`infrastructure/layout_planner.py`. Une requête (tâche `template_extract`, routée par
`LLM__TASKS__TEMPLATE_EXTRACT` — aujourd'hui `anthropic:claude-opus-5-5` ; Gemini ou OpenAI
par la même variable, sans livraison), température 0, schéma imposé (`LayoutPlan`) : l'image de
la page, les lignes (`L<n>` : texte, police, corps, couleur, boîte en mm), les formes (`D<n>` :
filets, cadres, fonds — pas les puces, que le code reconnaît seul), les images (`I<n>`). Le modèle
rend : l'en-tête (quelles lignes, quel champ, la photo), les bandes → colonnes → rubriques
(`ref`, genre, lignes du titre — aucune pour une rubrique sans titre, deux pour un titre sur deux
lignes —, éléments avec le rôle de chaque ligne, rôles posés dans une colonne latérale de frise),
l'ancrage de chaque forme (`header`, `page`, `flow`, `band`, `band_separator`, `section`,
`item`, `after_section`), et la fiche de galerie (titre, catégorie, description, étiquettes,
forme de la photo).

Il dit **qui est quoi et qui s'ancre à quoi** ; il ne pose aucun millimètre. Les textes du PDF
voyagent entre balises comme des données (ADR-0013) ; la sortie est revalidée par Pydantic, puis
par `check_plan` : chaque ligne doit figurer exactement une fois (une ligne oubliée bloque la
publication, comme une ligne sans rôle avant ; un doublon garde sa première place ; un
identifiant inconnu est ignoré). Le schéma envoyé ne porte ni borne numérique ni motif (le mode
strict d'Anthropic les refuse) ; les bornes se vérifient en Python (`ref` : lettres, chiffres,
tirets, 32 caractères au plus). Le plan complet est gardé avec la version (`labels["plan"]`),
à côté des étiquettes d'avant (rôle, rubrique, élément par ligne) que la couche de mesure lit.

`VisionLineLabeler` (v1) reste dans `llm.py` : il n'est plus branché.

### 2. L'assembleur bâtit la spécification ; le code garde toutes les mesures

`infrastructure/spec_assembler.py` (`SpecAssembler`, spécialisation de `SpecBuilder`) : même
forme JSON, même contrat pour les moteurs. La mesure d'une rubrique (gabarit, puces,
pictogrammes, décor d'élément) reste celle de `spec_builder.py`, étendue à :

- une rubrique **sans titre** (`untitled`) : elle commence à sa première ligne ;
- une rubrique **libre** : genre `other:<slug du titre>` (`other:section-<n>` sans titre),
  titre gardé, éléments dans `sample["others"]` ; la vue de l'exemple la rend telle quelle ;
- une **frise** : `side` (`dx_mm` négatif, largeur, alignement, emplacements et marges) ; le
  contenu garde ses emplacements ; l'élément se rend en rangée (colonne latérale, gouttière,
  contenu), sa hauteur est celle du plus haut des deux, la pastille s'attache au contenu ;
- un champ d'en-tête sur **plusieurs lignes** : un seul élément (boîte = ligne la plus longue
  + 1,5 mm, interligne mesuré, alignement des lignes) ; l'exemple reprend la place exacte de
  chaque ligne (`sample_lines`), le CV d'une personne replie dans la boîte ;
- des coordonnées d'en-tête **alignées à droite** ou centrées sur un voisin : rendues ainsi
  (`rx_mm`, `cx_mm`) ; la place d'un tel champ s'étend vers la gauche ;
- les bandes commencent **là où le PDF les commence** (le haut de leur rubrique la plus haute),
  les colonnes se mesurent par leurs lignes (+ 0,5 mm au bord droit : une police servie d'une
  chasse à peine différente replierait un mot plus tôt) ;
- un filet le long d'une bande (`band`, étiré avec elle), le long de tout le contenu
  (`flow`), un séparateur de bandes (`band_separator`), un fond fixe (`page`) ; une forme que le
  plan ancre à une rubrique et que la mesure n'a pas rangée est ancrée à la rubrique ;
- la photo est celle que le plan désigne ; l'exemple la pose **à son échelle réelle** dans le
  cadre (Canva zoome une grande image dans un petit cadre), la photo d'une personne couvre le
  cadre ;
- **une famille source → une famille servie** (`FontLibrary.resolve_group`) : le romain le plus
  proche de 400 décide de la remplaçante, les autres graisses et styles suivent ; la fiche d'une
  famille se retrouve par le nom qu'elle sert ; graisse et italique sont lus dans la police
  incorporée (table `OS/2`), pas devinés par pdfium ;
- la **référence** d'une mesure répétée sur deux éléments est le premier (la moyenne mettait les
  deux à côté : un modèle Canva dont le second élément est décalé de 4 mm), la médiane au-delà ;
- aucune ligature ni alternance contextuelle au rendu (`font-variant-ligatures: none`,
  `calt 0`) : un glyphe substitué par Chromium sort sous un code privé, illisible par un ATS ;
- les **étiquettes fixes** du modèle (rôle `label` : « Phone », « Email : ») s'attachent au champ
  qu'elles annoncent, au-dessus (pas mesuré) ou devant (écart mesuré) ; elles ne sont pas des
  données, les deux moteurs les écrivent depuis la spécification ;
- un **style de titre par rubrique** (`title_style`) quand un titre diffère du titre partagé
  (blanc sur une colonne sombre, foncé à côté) ;
- un paragraphe **aligné à droite ou centré** est reconnu (gauches dispersées, droites ou
  centres égaux) et rendu ainsi ; justifié reste justifié ;
- le **bord droit d'une colonne** est celui de sa ligne la plus longue rendue avec la police
  servie et l'interlettrage mesuré (résidu ligne par ligne + 0,3 mm) : ni repli prématuré ni
  mot gagné sur la ligne suivante ;
- les ancrages du **plan priment** sur la règle déterministe (un filet de frise pris pour le
  connecteur d'un élément se prolongeait de 112 mm) ; un rôle posé par un emplacement composé
  (« Poste | 2016 ») ne se répète pas par son emplacement seul ;
- les petites formes (< 8 mm : puces, pastilles, pictogrammes) ne sont pas proposées au
  modèle : le code les reconnaît (`markers_for`, `icons_for`, décor d'élément).

Les noms de classes CSS sont générés (`css_class` : lettres, chiffres, tirets) : rien de ce qui
vient d'un plan n'arrive tel quel dans le HTML, et `other:` n'est pas un sélecteur valide.

**Ce que le lot a appris à l'assembleur (2026-11-10, treize modèles)** — chaque règle est
mesurée sur un modèle nommé, et le plan reste ce qu'il est : le code tranche là où il se trompe.

- *Le plan se trompe de côté, la règle tranche* : des coordonnées déclarées dans l'en-tête
  alors qu'une rubrique Contact **titrée et vide** les attend plus bas y retournent, en un
  élément (cv9, Sacha : posées en absolu, elles chevauchaient la rubrique suivante d'un vrai
  CV) ; à l'inverse, une rubrique Contact **sans titre, seule dans la dernière bande, toute
  dans le bas de la page** (sous 85 % de la hauteur) est un pied de page : ses lignes
  deviennent des champs d'en-tête posés en absolu (cv5 : en bande, elles coulaient sous le
  contenu et le tassaient de 20 %). Le plan gardé dans les étiquettes porte le déplacement.
- *Étiquette au-dessus* : la valeur se pose au **pas mesuré de haut en haut** (la boîte de
  l'étiquette retirée) ; elle se posait au pas sous l'étiquette, 4 mm trop bas (cv1).
- *Éléments côte à côte* (deux références) : une **grille d'éléments** (`item_columns`,
  `item_pitch_mm`), chaque emplacement mesuré depuis la colonne de son élément, deux voisins
  de rangée sans écart ; le HTML les range en rangées au pas mesuré ; les coordonnées d'un
  élément (`extra` : téléphone, courriel d'une référence) restent dans l'exemple (cv1).
- *Anneau de frise* : toutes les formes posées sur une même ligne se dessinent, dans l'ordre
  de peinture (un rond sombre puis son cœur blanc ; la seconde écrasait la première, cv1).
- *Espacement des mots d'un paragraphe justifié* : lu sur sa dernière ligne (sa largeur moins
  celle que la police servie lui donne, répartie sur ses espaces ; 0,1 à 1,5 mm, jamais sur
  une police substituée) et écrit dans le style (`word_spacing_mm`, `word-spacing` CSS, compté
  dans toutes les largeurs) : sans lui, Chromium remontait un mot que Canva avait replié
  (cv1 : 0,49 mm sur un corps de 8 pt, mots à 2 mm de 0,48 → 1,00).
- *Paragraphe au fer à droite ou centré* : la suite d'un paragraphe partage sa marge gauche,
  **ou sa marge droite, ou son axe** (`elements`) ; sa boîte s'arrête au bord droit mesuré, comme
  un justifié ; une boîte garde 0,3 mm au-dessus de sa ligne la plus large (Chromium repliait
  son dernier mot sur un arrondi : « Projet » tombait d'une ligne, Sacha) ; chaque ligne se
  mesure à son **propre corps** (une puce plus petite que son style ne mesure pas au corps de
  l'emplacement, cv5) ; une colonne s'arrête 1 mm avant le bord de la page quoi que disent
  ses lignes (cv5 : 211,6 mm sur une page de 210).
- *Poste visé de flux* : sans place entre le nom et la première bande (le profil suit le nom,
  cv9/Sacha), le poste visé synthétisé **ouvre le flux** (`flow`) et pousse la première bande
  — pour une personne seulement, l'exemple n'en a pas.
- *En-tête d'une personne* : un nom se réduit jusqu'à 35 % avant de se replier (« Moreau-Lefebvre »
  dans un modèle en 48 pt) ; un filet vertical à sa hauteur borne un champ ; des coordonnées
  d'en-tête dont la ligne de plus (le site) entrerait dans la rubrique du dessous se
  **resserrent** (`tight_contact`) ; la marge de la première rubrique d'une colonne ne descend
  pas sous la **réserve** que l'en-tête occupe au-dessus d'elle, même resserrée par l'ajustement
  (`header_reserve` : « Expériences » remontait de 40 % sous le site, Sacha) ; une coordonnée
  trop longue dans sa colonne se réduit avant de se couper ; le titre d'une personne se replie
  dans sa colonne (l'exemple garde sa ligne mesurée).
- *Propre à l'exemple* (`sample_only`) : les barres de niveau d'une liste (rect ≤ 3 mm de haut
  dans skills/languages/interests ; une personne a d'autres langues), un tracé rastérisé qui
  entoure une image (le QR code de cv3). Les deux moteurs ne les dessinent que pour l'exemple.
- *Tracés* : un tracé complexe et grand (≥ 8 mm, courbe non ronde) sans texte dessous se rend en
  **image** rastérisée depuis la source (`asset`) — les moteurs ne tracent que rectangles, ronds
  et filets : le cadre carré d'un QR code devenait un disque plein ; un rond doit toucher ses
  quatre bords (`is_round`) ; la boîte d'un tracé plein ≤ 60 mm est celle de ses **pixels peints**
  (`tighten`) : pdfium la bornait par ses points de contrôle, le surligneur d'un titre se
  dessinait sur le texte (cv3) ; la puce d'un élément se dessine devant le rôle qu'elle marquait
  (`marker["roles"]`, l'année d'une expérience, cv5).
- *Lecture du PDF* : un glyphe réimprimé exactement au même endroit (un faux gras, une ombre)
  ne compte qu'une fois (« MMoonn ppoorrttffoolliioo », cv3) ; une espace du PDF coupe un mot
  même étroite (0,15 em dans une police condensée : « ÀPROPOSDEMOI », cv3) ; une rubrique dont
  toutes les lignes sont des étiquettes est du texte ; un intitulé écrit sur deux lignes garde
  son retour à la ligne dans l'exemple (`pre-line`), un paragraphe se replie dans sa boîte ;
  un élément de l'exemple écrit à un autre corps que son style (> 10 %) garde le sien
  (`sizes`, cv5 : la troisième expérience en 5,5 pt pour 7,9) ; un poste écrit deux fois
  (« Graphiste » en rappel, « GRAPHISTE » en grand) garde sa casse écrite dans l'exemple.

### 3. Une boucle de correction bornée, la meilleure passe gardée

`infrastructure/ingestion_loop.py`, appelée par `KarriaDocumentEngine.analyze` : plan →
spécification → rendu de l'exemple (Gotenberg) → mesure → porte. Si la porte échoue ou qu'une
ligne n'est pas placée, **une** passe de correction : le modèle reçoit son plan, le côte à côte
source | rendu (`shared.images.side_by_side`), les lignes hors de leur place (> 2 mm, avec leur
décalage, ou absentes du rendu), les formes manquantes, les lignes non placées
(`infrastructure/diagnostics.py`), et rend un plan complet corrigé. On réassemble, on rend, on
remesure ; la meilleure passe est gardée (score, puis moins de lignes non placées) ; une panne de
la passe 2 ne perd pas la passe 1. **Deux requêtes au plus par version.** Chaque passe est
journalisée dans `report["passes"]` (score, composantes, verdict, jetons, latence, modèle).
Rien n'est publié sous le seuil : la version reste `review` avec le détail.

### 4. La porte ne compte pas en faute une substitution de police déclarée

Une famille source servie par une autre famille (`fonts_report`, `origin = substitute`) est
journalisée, listée dans la revue et dans la fiche de version ; la porte (`fidelity.measure`,
`substituted=`) tient ses mots pour concordants en police. Sans cela, un modèle en Canva Sans —
police propriétaire, jamais servable — plafonnait à `style = 0,67` par construction, quel que
soit le dessin. La substitution reste une faute de **revue** (visible), pas une faute de
**mesure**. Une famille absente sans déclaration reste une faute.

### 5. Ce que ça coûte

Une passe = une requête : ~10,7 k jetons en entrée (image de la page comprise), ~1,3–1,5 k en
sortie, 10 à 14 s avec `claude-opus-5-5` (effort bas, ADR-0018). La passe de correction porte
en plus le plan et le côte à côte : ~15,4 k en entrée. Un modèle coûte donc **une ou deux
requêtes**, jamais plus ; les rendus et mesures (Gotenberg, numpy) prennent 15 à 40 s par passe
sur une page dense.

## Mesures (2026-11-10, `claude-opus-5-5`, Gotenberg 8.38.1 local, lot complet réingéré)

Score v1 = étiquetage ligne à ligne de l'ADR-0020 ; v2 = la passe gardée de l'ingestion v2
(p1 : le plan ; p2 : la correction) ; jetons = entrée / sortie, par passe. Un vrai CV (fixture
complète : Langues, Projets, Intérêts, nom composé) tient sur **une page sans chevauchement** dans
chacun des dix modèles publiés.

| Modèle | v1 | v2 (passe gardée) | SSIM tol. / mots ≤ 2 mm / style / décor | pages | jetons (entrée / sortie) | verdict |
|---|---|---|---|---|---|---|
| romain-dupont (Canva) | 0,52 | **0,945** (p1) | 0,895 / 0,968 / 1,00 / 1,00 | 1 | 11 369 / 1 526 | publié |
| sacha-dubois (Canva) | 0,51 | **0,916** (p1) | 0,866 / 0,904 / 1,00 / 1,00 | 1 | 10 957 / 1 344 | publié (Canva Sans → Inter déclaré, plancher 0,84) |
| meridian (original) | 0,92 | **0,933** (p1) | 0,905 / 0,942 / 1,00 / 0,94 | 1 | 12 016 / 1 730 | publié |
| minimalist (original) | 0,92 | **0,924** (p1) | 0,890 / 1,00 / 1,00 / 0,82 | 1 | 11 134 / 1 571 | publié |
| marketing (original) | 0,92 | 0,916 (p2) | 0,858 / 0,992 / 1,00 / 0,88 | 1 | 12 344 + 17 043 / 1 776 + 1 776 | revue — score ≥ 0,90 mais SSIM 0,858 < 0,88 (filets gris pleine largeur, second titre « LANGUAGES » du modèle) |
| cv1 | — | **0,949** (p1) | 0,889 / 1,00 / 0,99 / 1,00 | 1 | 13 198 / 1 925 | publié (était 0,649) |
| cv2 | — | **0,921** (p1) | 0,881 / 0,992 / 1,00 / 0,84 | 1 | 11 605 / 1 655 | publié |
| cv3 | — | 0,867 (p1) | 0,805 / 0,962 / 0,97 / 0,79 | 1 | 10 459 + 14 895 / 1 446 + 1 446 | revue — Biro Script Plus et TT Commons Pro substituées (non libres), Bebas Neue homonyme aux métriques verticales différentes (plantait avant) |
| cv5 | — | **0,907** (p1) | 0,908 / 0,909 / 1,00 / 0,81 | 1 | 10 628 / 1 319 | publié (était 0,639) |
| cv6 | — | **0,921** (p1) | 0,887 / 0,980 / 1,00 / 0,85 | 1 | 11 515 / 1 549 | publié |
| cv8 | — | **0,950** (p1) | 0,907 / 0,984 / 1,00 / 0,97 | 1 | 12 120 / 1 656 | publié |
| cv9 | — | **0,927** (p1) | 0,912 / 0,957 / 1,00 / 0,85 | 1 | 11 111 / 1 548 | publié |
| cv10 | — | 0,766 (p1) | 0,792 / 0,440 / 1,00 / 1,00 | 1 | 13 196 + 19 813 / 1 820 + 1 861 | revue — trois familles substituées (CatchyMager, Hatton : fontes Canva non libres ; Glacial Indifference absente de la bibliothèque) : les mots se replient ailleurs |

Lecture : cv4 et cv7 sont les mêmes modèles Canva que sacha-dubois et romain-dupont (lettre jointe
en plus) ; un modèle coûte 10–13 k jetons en entrée et 1,3–1,9 k en sortie par passe (10–15 s), la
passe de correction 15–20 k en entrée ; sur la journée du 10, cv1/cv5/cv10/marketing/cv3 ont été
réingérés deux fois (correctifs entre les deux) et cv3 a eu un plan seul en plus : 2 à 6 requêtes
par modèle, 1 ou 2 pour la version publiée.

## Conséquences

- Aucune migration : la spécification reste du JSON de même forme ; le plan vit dans
  `labels["plan"]`, les passes dans `report["passes"]`.
- Le port `DocumentEngine.measure` reçoit `substituted=` ; `Analysis` porte `passes`.
- `scripts/templates.py dump VERSION_ID DIR` écrit les artefacts d'une revue (source, rendu,
  côte à côte, rapport, spécification, plan).
- Le DOCX rend une frise en empilant la colonne latérale au-dessus du contenu, et pose les
  filets de bande à leur hauteur mesurée (Word n'étire pas une forme avec un tableau) :
  `PLUS-TARD.md`.
- Ce qui reste ouvert : les rubriques libres d'un **vrai** CV (le CV v2 n'a pas de rubrique
  libre : elles n'apparaissent que dans l'exemple) ; un poste visé replié dans sa boîte pour une
  personne, placé ligne par ligne pour l'exemple ; le plancher de SSIM tolérant sous une
  substitution de police (voir Mesures).

## Alternatives écartées

- **Garder l'étiquetage ligne à ligne et améliorer les heuristiques de bandes** : chaque
  nouveau dessin (frise, profil sans titre, coordonnées sous la photo) demandait une règle de
  plus, et deux règles se contredisaient déjà (en-tête = au-dessus du premier titre, contact
  sans titre). La structure est exactement ce qu'un modèle de vision voit d'un coup d'œil.
- **Laisser le modèle poser des millimètres** (positions, largeurs) : c'est ce que la V1 faisait
  en lui faisant écrire du HTML (fidélité 0,32) ; la géométrie mesurée est exacte, la sienne ne
  l'est jamais.
- **Boucler jusqu'au seuil** : la deuxième passe ne change le plan que si la structure est en
  cause ; au-delà, ce sont des mesures (polices, chasse) que le modèle ne corrige pas. Deux
  requêtes bornent le coût d'un modèle qui n'y arrivera pas.
