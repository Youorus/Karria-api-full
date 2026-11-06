# ADR-0036 — La lettre de motivation : la lettre appariée au modèle, ou la lettre sobre

**Statut :** Proposée — 2026-11-06 (écrite par Fable, à valider par Marc)
**Remplace partiellement :** ADR-0030 § 1 (« pas de modèle de lettre, la lettre se rend
dans la spécification du modèle de CV ») et § 4 (les moteurs lisent la vue dans la
spécification du CV)
**Complète :** ADR-0020 (spécification canonique, porte de qualité), ADR-0029 (rendu HTML
ancré), ADR-0033 (dossiers), ADR-0034 (couleur principale)

## Le problème

Marc : « L'utilisateur doit avoir la possibilité d'utiliser une lettre de motivation de la
template ou une lettre de motivation générique dans le neutre : une lettre sur page
blanche avec la structure d'une lettre bien comme il faut (objet, tout ce qui va avec, le
corps, et à la fin la signature par son nom), bien écrite, dans la bonne police. Si le
modèle de CV a un modèle de lettre, il peut l'utiliser ; sinon il utilise le format blanc.
[…] Lors de la création des templates, on doit pouvoir ajouter le modèle visuel de la
lettre qui va avec le CV. Ce que l'utilisateur visualise dans la galerie (CV, et lettre
appariée s'il y en a une, sinon notre lettre blanche) doit être 100 % ce qu'il obtient à
l'édition. Rien ne doit manquer. Tout en interne, sans dépendre d'éléments extérieurs ;
l'IA n'intervient que là où elle est indispensable. »

L'ADR-0030 avait choisi de **ne pas avoir de modèle de lettre** : la lettre se dessinait
dans la grammaire du CV (ses marges, ses polices, sa couleur d'accent). C'était une
approximation : les modèles Canva vendent le CV **avec** sa lettre (`~/Desktop/cv/cv1/`),
dessinée à part — même nom en grand interlettré, coordonnées à gauche, destinataire et
date à droite, objet en gras italique, cinq paragraphes justifiés —, et ce dessin-là ne se
déduit pas du CV. Et un modèle sans lettre donnait une lettre « dans la grammaire du
CV » qui ne ressemblait à rien de connu : ni au modèle, ni à une lettre d'affaires.

## La décision

### 1. Une lettre se rend toujours depuis une **spécification de lettre**

Même forme que la spécification du CV (ADR-0020 § 1), mesurée, immuable par version :

- `page`, `fonts`, `styles` par rôle (`header.first_name` / `header.last_name` ou
  `header.full_name`, `header.headline`, `header.address|phone|email`,
  `recipient.company`, `recipient.city`, `date_line`, `subject`, `greeting`,
  `paragraph`, `closing`, `signature`), chacun avec famille servie, graisse, corps,
  couleur, interlettrage mesuré sur la police servie, capitales, boîte et pas ;
- `header.blocks` : des **piles** ancrées à gauche, à droite ou au centre (`x_mm`,
  `right_mm` ou `cx_mm`, `y_mm`, `max_w_mm`), dont les lignes s'empilent au pas mesuré
  (`pitch_mm` par élément) — une ligne vide disparaît sans laisser de blanc ;
- `body` : la colonne de texte (`x_mm`, `w_mm`, `top_mm`) et ses blocs dans l'ordre du
  modèle (`subject` avec son préfixe tel que le modèle l'écrit, `greeting`, `paragraph`
  avec `gap_between_mm` et la justification détectée, `closing`, `signature`), chacun
  avec son blanc mesuré (`gap_before_mm`) ;
- `page_decorations`, `content_bottom_mm` (la borne du script d'ajustement).

Deux sources :

- **(a) la lettre appariée** d'un modèle, ingérée depuis son PDF
  (`infrastructure/letter_spec_builder.py`) ;
- **(b) la lettre sobre** intégrée (`domain/plain_letter.py`) : A4, marges de 25 mm,
  expéditeur en haut à gauche (nom 15 pt gras, intitulé, coordonnées), destinataire à
  droite (entreprise en gras, ville), lieu et date à droite, « Objet : … » en gras,
  civilité, paragraphes justifiés séparés d'une ligne, formule de politesse, signature
  par le nom. **Carlito 11 pt**, la jumelle métrique de Calibri : la police par défaut
  de Word depuis 2007, donc celle de la plupart des lettres de candidature que les
  recruteurs lisent ; ses formes humanistes et sa grande hauteur d'x se lisent mieux en
  paragraphes justifiés que la linéale d'Arial (Arimo, plus froide et plus large :
  chasse 0,577 em contre 0,503) ; et un DOCX ouvert dans Word avec Calibri garde ses
  retours à la ligne. Pas de ligne du texte courant 5,4 mm (11 pt × 1,39), interlignes
  mesurés sur la police (boîte 1,22 × le corps). **Aucune couleur** : la lettre sobre
  reste neutre, `recolor_spec` n'y trouve pas de couleur principale.

Les moteurs `LetterHtmlRenderer` / `LetterDocxRenderer` lisent désormais cette
spécification (adaptés, pas dupliqués). Le DOCX pose chaque ligne de base là où la
spécification la met, d'après la règle **mesurée** de LibreOffice et Word pour un
interligne exact `L` et une boîte `B` : `L ≥ B` → l'excédent va au-dessus, la ligne de
base tombe à `L − D` ; `L < B` → la boîte est réduite à l'échelle, la ligne de base à
`L × A / B`. Un blanc négatif n'existe pas : une ligne qui devrait commencer au-dessus de
la précédente est raccourcie (le nom en grand corps, au pas plus serré que sa boîte).

### 2. L'ingestion de la lettre appariée : même chaîne, même mesure, même porte

`POST /internal/templates` accepte un second fichier facultatif `letter`
(`python -m scripts.templates submit … --letter F.pdf`), gardé dans
`templates/<id>/v<n>/letter_source.pdf`. Dans le même travail de file que le CV :

lecture (`pdf_reader`) → polices résolues (la même résolution que le CV, puis **une
seule remplaçante par famille du modèle** : « TT Commons Pro » normal tombait sur Barlow
et son gras sur DM Sans) → rôles des lignes par le modèle de vision
(`LetterLineLabeler`, tâche `template_extract`, rôles `sender_name`, `sender_headline`,
`sender_contact`, `recipient_name`, `recipient_address`, `date_line`, `subject`,
`greeting`, `paragraph` + numéro, `closing`, `signature`, `other` ; le texte des lignes
voyage entre balises, en donnée) → spécification de lettre → rendu de l'exemple de la
lettre → **`fidelity.measure`** et **`gate.evaluate`**, identiques au CV → DOCX mesuré
(informatif) → aperçus (`letter/vignette.webp`, `apercu.png`, `apercu_pleine_page.png`,
BlurHash). Si la lettre passe sous le seuil, **la version entière reste `review`**
(`gate.failures` préfixés « lettre : »). Gardé sur la version (migration 21) :
`letter_source_key`, `letter_spec`, `letter_sample` (date imprimée comprise, c'est avec
elle que la fidélité se mesure), `letter_labels`, `letter_thumbnail_key`,
`letter_thumbnail_blurhash`, `letter_preview_key`, `letter_full_key` ;
`report["fidelity"]["letter"]`, `["letter_docx"]`, `report["fonts_letter"]`,
`report["letter_gate"]`.

Ce que le modèle n'a pas reçoit un emplacement **dans sa grammaire**
(`complete_spec`) : l'intitulé prend le style des coordonnées sous le nom, une
coordonnée manquante suit la dernière, la ville du destinataire suit l'entreprise, la
date se pose à droite sous le destinataire, l'objet / la civilité / la formule / la
signature prennent le style des paragraphes (l'objet en gras). Rien n'est omis.

La spécification de la lettre appariée garde en `accent` la couleur du **décor** du CV
apparié (`primary_color_of`, ADR-0034 § 2026-11-06) et marque `follows_decor` chaque style
de texte qui, dans le PDF, avait exactement cette couleur (l'objet, souvent). Au rendu,
`recolor_letter_spec` (`domain/letter.py`) donne la couleur choisie pour le CV aux
décorations de la lettre qui portaient le décor ou l'une de ses teintes et aux textes
marqués ; tout autre texte reste du texte — **le CV et la lettre d'un dossier changent
ensemble, et de la même façon**. (Fusion avec `feat/couleurs` le 2026-11-06 : la première
version lisait `styles.section_title.color` comme accent de l'objet.)

### 3. Le choix : `letter_style ∈ {"template", "plain"}`

Sur `RenderRequest` (lettre) et `LetterExportRequest` ; retenu sur le dossier
(`applications_dossiers.letter_style`, **`PUT /applications/{id}/letter-style`**
`{ "letter_style": "template" | "plain" | null }`, `ChangeDossierLetterStyle`, route sœur
de `PUT /{id}/color`) et sur l'export (`documents_generated_cvs.letter_style`).
Résolution (`domain/plain_letter.py › letter_style_served`) : `template` sans lettre
appariée → `plain` ; par défaut `template` si le modèle en a une, sinon `plain`. La
réponse dit toujours le style **réellement servi** : `RenderRead.letter_style_served`,
`GeneratedCvRead.letter_style`. La couleur choisie s'applique à la lettre appariée via
`recolor_spec` ; la lettre sobre reste neutre.

### 4. La galerie montre exactement ce que l'atelier rend

`GET /templates/{id}` (et la liste) remplissent `cover_letters` : la lettre appariée
(`id: "template"`, « Lettre assortie », aperçus signés, BlurHash), sinon **la lettre
sobre** (`id: "plain"`, « Lettre sobre »), rendue une fois avec son contenu d'exemple
(`PLAIN_LETTER_SAMPLE`, fictif) par la commande **`python -m scripts.templates
plain-letter`**, qui télécharge aussi ses polices (Carlito 400 et 700) et dépose les
aperçus sous `plain-letter/v<PLAIN_LETTER_VERSION>/` du bucket `templates`, puis
`meta.json` (BlurHash) en dernier — la galerie le lit une fois par processus et par dix
minutes ; tant qu'il n'existe pas, aucune page de lettre. `has_letter` dit si la lettre
est assortie ; `pages` porte `style`. L'app affiche la page avec le libellé du serveur
(« Lettre assortie » / « Lettre sobre »), et ne parle de « lettre assortie » dans la fiche
que si elle existe.

### 5. La mesure de fidélité juge la police **servie**, quand la substitution est déclarée

`fidelity.measure` reçoit le rapport de polices de l'analyse (`served_families`) : un
mot rendu dans la remplaçante déclarée d'une police non libre compte comme « de la
bonne police ». Sans cela, tout modèle aux polices propriétaires (Canva : TT Commons
Pro, The Seasons, Canva Sans, Garet) tombait à `style ≈ 0,67 < 0,95` **par construction**,
quelle que soit la qualité du rendu. La substitution reste journalisée et dans le rapport
de version ; la mesure vaut pour le CV comme pour la lettre. **Décision à valider par
Marc** : elle rend publiables des modèles dont la police est remplacée (la composante
« style » ne les retient plus), ce qui était déjà l'intention de l'ADR-0020 § Mise en
œuvre (« chaque substitution est journalisée et rangée dans le rapport »).

## Ce qu'on a mesuré (2026-11-06, Gotenberg 8.38.1 local, étiquetage par Anthropic)

Lettre appariée de `cv1` (« Sacha Dubois », trois polices Canva propriétaires, toutes
substituées : TT Commons Pro → Barlow, The Seasons → Montserrat, Canva Sans → Inter) :

| | score | SSIM tolérant | mots à ≤ 2 mm | style | décor |
|---|---|---|---|---|---|
| PDF (Chromium) | **0,905** | 0,820 | 0,946 | 0,996 | 1,0 |
| DOCX (aperçu LibreOffice) | **0,920** | 0,825 | 0,996 | 0,996 | 1,0 |

Lignes de base à ±0,4 mm (HTML) et ±0,6 mm (DOCX) sur les 28 lignes ; interlettrage du
nom (10,4 pt) et de l'intitulé (3,9 pt) retrouvés ; les retours à la ligne des
paragraphes justifiés tombent au même mot grâce à la compensation de chasse
(l'interlettrage négatif constant d'une remplaçante plus large n'est plus pris pour du
bruit). **Seul le SSIM tolérant retient la lettre** (0,820 < 0,88) : des glyphes d'une
autre famille ne dessinent pas la même page, et aucun réglage de mise en page n'y change
rien. Sans police libre, 100 % n'est pas atteignable ; la version reste donc `review`,
avec le CV `cv1` lui-même (0,50, hors de ce lot). Voir « Ce qui reste ouvert ».

## Ce qu'on a écarté

- **Garder la lettre « dans la grammaire du CV »** (ADR-0030 § 1) comme repli pour un
  modèle sans lettre : une lettre à 10 pt dans les marges d'un CV à deux colonnes ne
  ressemble ni au modèle ni à une lettre ; la lettre sobre est une vraie lettre.
- **Faire dessiner la lettre sobre par un modèle de langage** : rien à décider ; la mise
  en page d'une lettre d'affaires française est connue, elle s'écrit en données.
- **Une couleur sur la lettre sobre** : « dans le neutre » ; elle reste noire sur blanc.
- **Un `PUT /applications/{id}/template` qui porterait aussi le style** : deux gestes,
  deux idempotences (même raison que la couleur, ADR-0034).
- **Baisser le plancher du SSIM pour les polices substituées** : un seuil ne se baisse
  pas sans Marc (AGENTS § 8) ; la mesure dit ce qu'elle voit.

## Ce qui reste ouvert

- **Les polices non libres.** Une lettre (ou un CV) Canva en TT Commons Pro, The
  Seasons, Garet ne passera jamais `ssim_tolerant ≥ 0,88`. Trois voies, au choix de
  Marc : acheter / fournir ces polices (elles entreraient dans la bibliothèque comme les
  libres, et le SSIM devrait passer) ; accepter une tolérance de SSIM quand toutes les
  substitutions sont déclarées (une seconde porte, à écrire dans une ADR) ; publier en
  revue manuelle.
- **Le CV `cv1`** (0,50) est repris par l'agent « ingestion v2 » ; la lettre appariée
  ne sera visible dans la galerie que quand la version entière passera.
- La **résolution des polices du CV** juge police par police (normal et gras d'une même
  famille peuvent tomber sur deux remplaçantes) ; la lettre unifie, pas encore le CV
  (`PLUS-TARD.md`).
- `letter_style` n'existe pas sur le **choix par défaut** (`Selection`) : une lettre
  n'existe que dans un dossier (ADR-0033 § 5), le dossier suffit.

## Conséquences

- **Migration 21** (`lettres_appariees`) : huit colonnes `letter_*` NULL sur
  `documents_template_versions`, `letter_style` VARCHAR(8) NULL sur
  `documents_generated_cvs` et `applications_dossiers` — avant le code.
- **Commande d'exploitation** `python -m scripts.templates plain-letter`, à lancer une
  fois par environnement (prod comprise) et à chaque changement de
  `PLAIN_LETTER_VERSION` ; Gotenberg joignable.
- Contrats : `docs/contrat-api-documents.md` (`letter_style`, `letter_style_served`),
  `docs/contrat-api-candidatures.md` (`PUT /{id}/letter-style`, `letter_style`),
  `karria_app/docs/contrat-api-modeles.md` (`cover_letters`, `has_letter`, `pages[].style`).
- `domain/fonts.py` lit aussi les abréviations de graisse des jetons de style
  (`-Rg`, `-Bd`, `-BdIt`, `-Md`…), ramène une graisse hors échelle (1 264) à 400 et
  retire la taille optique d'un nom de famille (« Inter 18pt » → « Inter », qui faisait
  manquer la police au rendu).
