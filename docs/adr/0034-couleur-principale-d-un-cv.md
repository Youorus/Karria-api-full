# ADR-0034 — La couleur principale d'un CV, choisie par la personne : une transformation pure de la spécification, un choix retenu par dossier et par défaut

**Statut :** Proposée — 2026-11-05 (écrite par Fable, à valider par Marc)
**Complète :** ADR-0020 (spécification canonique d'un modèle), ADR-0029 (rendu
HTML ancré), ADR-0030 (la lettre dans la grammaire du modèle), ADR-0033 (dossiers
de candidature)

## Le problème

Marc : « Si un CV a une couleur primaire de bleu, que la personne puisse, vu
qu'on a un mode d'édition, mettre une couleur primaire de son choix — une
palette prédéfinie, un code couleur, ou un sélecteur avec des variantes — et
revenir à la couleur initiale si elle n'est pas satisfaite. »

Un modèle est une spécification JSON **mesurée** dans un PDF (ADR-0020) : ses
couleurs sont écrites style par style (`styles.*.color`), sur la puce
(`marker.color`), sur les filets et bandes (`decorations[].fill/stroke`,
`page_decorations`, `header.decorations`), sur les séparateurs. La « couleur
principale » que la galerie affiche est celle des titres de rubrique
(`primary_color` = `styles.section_title.color`). Changer la couleur d'un CV,
c'est donc changer **cette** couleur partout où elle sert, et seulement elle :
le texte noir reste noir, un filet gris reste gris.

## La décision

1. **Une transformation pure, non destructive**, `recolor_spec(spec, primary)`
   (`documents/domain/palette.py`), à la manière d'`ats_safe_spec` : elle parcourt
   la spécification et remplace toute occurrence de la couleur d'origine
   (`primary_color_of(spec)`) par la couleur choisie, sous les clés `color`,
   `fill`, `stroke`, `background`. Les **teintes dérivées** — même teinte (± 10°),
   non grises — sont déclinées depuis la nouvelle couleur avec le **même écart de
   clarté** et la même proportion de saturation : la bande pâle d'une colonne
   latérale reste pâle, un filet discret reste discret. Un modèle dont la couleur
   principale est un gris n'a pas de teinte à suivre : seules les occurrences
   exactes changent. L'original n'est jamais modifié ; sans couleur, ou avec la
   couleur d'origine, le même objet est rendu.
2. **Appliquée après `ats_safe_spec`**, dans `CvRenderer.prepare` (aperçu, export),
   `RenderDocument` (atelier, exemple, lettre) et `ExportLetter` : le PDF, le DOCX
   et le HTML ancré partent de la même spécification recolorée, donc le CV et
   la lettre d'un dossier portent la même couleur. Le score ATS ne lit pas les
   couleurs ; la variante « optimisée ATS » est mesurée sur la spécification
   d'origine, comme avant.
3. **Le code couleur** est `#rrggbb` (ou `rrggbb`, `#rgb`), normalisé en `#RRGGBB`
   par le schéma (`parse_hex_color`, `422` sinon). Il voyage dans
   `RenderRequest`, `ExportRequest`, `LetterExportRequest` et `SelectRequest`
   (`primary_color: str | null`).
4. **Le choix est retenu à trois endroits**, trois colonnes NULL (migration 20) :
   - `documents_selections.primary_color` : la couleur par défaut de la personne,
     avec le modèle choisi (`PUT /user-templates/me/selected`) ; `SelectionRead`
     rend aussi `default_primary_color`, la couleur d'origine du modèle, pour que
     l'app affiche « D'origine » ;
   - `applications_dossiers.primary_color` : la couleur du dossier,
     **`PUT /applications/{id}/color`** (`ChangeDossierColor`), route sœur de
     `PUT /{id}/template` ; elle **survit au changement de modèle** — un choix de
     la personne n'est pas un attribut du modèle — et `null` rend la main à la
     couleur du nouveau modèle ;
   - `documents_generated_cvs.primary_color` : la couleur avec laquelle l'export
     a été rendu, pour que « Mes CV » la montre et la réutilise.
5. **La galerie ne change pas** : `primary_color` d'un modèle reste la couleur
   mesurée, celle d'origine. Il n'existe pas de « modèle recoloré » : la couleur
   est une donnée de la personne, posée au rendu.

## Ce qu'on a écarté

- **Une couleur dans `PUT /applications/{id}/template`.** Un champ facultatif y
  aurait eu deux sens (absent = « ne touche pas », `null` = « retire ») que
  Pydantic distingue mal, et l'app aurait dû renvoyer la couleur à chaque
  changement de modèle. Deux routes, deux gestes, deux idempotences.
- **Une version recolorée du modèle en base** (une `TemplateVersion` par
  couleur). Une version est immuable et mesurée ; des milliers de couleurs
  possibles auraient multiplié les aperçus et les mesures pour rien.
- **Remplacer toutes les couleurs non noires.** Un modèle à deux accents (titres
  bleus, puces orange) aurait perdu son second accent ; on ne touche que la
  couleur principale et ses teintes.
- **Une palette fermée côté serveur.** La palette est un choix d'interface
  (l'app en propose une) ; le serveur accepte tout `#rrggbb`, sans juger la
  lisibilité — la personne voit le résultat en direct.

## Conséquences

- Les **pictogrammes de contact** des modèles, rastérisés depuis le PDF
  (`icons[*].file`, PNG), gardent leur couleur d'origine : une limite visible sur
  les modèles qui en ont, notée dans `PLUS-TARD.md` (teinter le PNG au rendu).
- Une couleur très claire sur titres clairs est permise : l'app montre le rendu
  en direct et offre « Revenir à la couleur d'origine ».
- Contrats : `docs/contrat-api-documents.md` § 1 et § 2, `contrat-api-candidatures.md`
  (`PUT /{id}/color`, `primary_color` sur le dossier et la carte), copie dans
  `karria_app/docs/`, `contrat-api-modeles.md` (`Selection`).

## Addendum du 2026-11-06 — version 2 : le décor change, le texte reste (Fable, à valider par Marc)

**Revient sur :** la définition de la couleur principale (§ Le problème, décision 1) et la
portée de `recolor_spec` ; le reste (retenue à trois endroits, routes, code couleur) ne change pas.

Marc, après avoir vu la v1 sur ses modèles : « La couleur primaire ne doit jamais toucher le
texte. Sur un CV à deux couleurs — texte noir, bande rouge — la couleur primaire change la
bande, les couleurs du document, mais pas la couleur du texte. Le texte, on peut le changer
seulement si c'est nécessaire : du texte noir sur un fond devenu noir ne serait pas visible,
donc il faut qu'intelligemment ces modifications soient faites. Donner le pouvoir de styliser
le CV de base à sa guise au niveau des couleurs, mais pas au niveau du style. »

Sur les modèles réels (Sacha Dubois, Romain Dupont, Meridian, Minimalist), titres et texte sont
noirs **et** le décor l'est aussi : la v1, qui définissait la couleur par les titres et
remplaçait toute occurrence, aurait recoloré le texte des titres — exactement ce que Marc refuse.

### Ce qui change

1. **La couleur du modèle est celle de son décor, mesurée.** `primary_color_of(spec)` est la
   couleur dominante — par **surface peinte** (mm²), à égalité par occurrences — des clés
   `fill`/`stroke`/`background` des décorations (bandes et filets d'en-tête et de page,
   décorations de rubrique, séparateurs `slots[].separator`) et de `marker.color` (la puce est
   une forme **dessinée** dans le PDF, pas un glyphe : `spec_builder.markers_for` la lit dans les
   tracés). Hors blanc du papier ; une couleur de clarté ≥ 0,85 n'est qu'un **fond** (bande pâle,
   filet discret) : elle suit comme teinte, elle ne définit pas — sauf si rien d'autre ne colore
   le décor. Sans décor coloré, la couleur des filets et des puces (souvent noir) est celle du
   décor ; sans décor du tout, `None` (la galerie n'affiche pas de couleur, l'app montre un rond
   vide). **Les titres de rubrique ne définissent plus la couleur.**
2. **`recolor_spec(spec, primary)` ne touche que le décor** : `fill`, `stroke`, `background` et
   `marker.color` ; occurrences exactes et teintes dérivées comme avant. La déclinaison d'une
   teinte garde désormais sa **position entre la couleur et le blanc** (ou le noir) plutôt qu'un
   écart de clarté additif : une bande pâle reste pâle quelle que soit la clarté choisie, et
   ne s'efface jamais dans le blanc. Un décor **gris** (noir compris) suit en entier, gris par
   gris (le filet gris clair d'un modèle noir devient un filet bleu clair) ; un gris dans un
   décor coloré reste gris ; une couleur dans un décor gris reste (second accent préservé).
   **Aucune clé `color` d'un style de texte ne change**…
3. **… sauf la règle de lisibilité.** Un texte posé **sur** une surface recolorée dont le
   contraste avec la nouvelle couleur passe sous **4,5:1** (WCAG 2, luminance relative) prend le
   blanc (`#FFFFFF`) ou le noir profond (`#111111`), celui qui se lit le mieux sur **toutes** les
   surfaces où ce style est posé (`legible_styles`). « Posé sur » se lit dans la géométrie de la
   spécification : un élément d'en-tête dont le point d'ancrage est dans le rectangle d'une
   décoration pleine (`header.decorations`, `page_decorations` ancrées à la page, dernière
   peinte au-dessus) ; une colonne couverte à 90 % en largeur et 80 % en hauteur de bande par
   une telle décoration, ou dotée d'un `background` ; une rubrique derrière une barre pleine
   ancrée (`anchor: section` au niveau du titre → le titre ; `attach: row` → le rôle marqué ;
   `anchor: item` → les rôles de la rubrique). Un style partagé entre une bande et le papier
   (`section_title` l'est toujours : un seul style pour tous les titres) ne peut pas être sauvé
   des deux côtés : il ne change que si son pire contraste atteint 4,5 ou double — sinon il
   garde sa couleur. Limite connue, documentée plutôt que contournée.
4. **Les pictogrammes de contact suivent.** Ils sont rastérisés à 600 dpi sur du blanc
   (`PdfDocument.render_png`) : `shared.images.single_ink` mesure si l'image est **monochrome**
   — une seule encre sur le fond (blanc ou transparent), les bords anticrénelés étant de l'encre
   diluée (chaque pixel sur le segment blanc → encre, à 12/255 près) ; `tinted_ink` (domaine)
   dit si cette encre est la couleur du décor (± 8/255) ou l'une de ses teintes, et laquelle
   elle prend ; `shared.images.tint_ink` redilue chaque pixel dans la nouvelle couleur, fond et
   alpha gardés. Appliqué à l'envoi par `application.tint_icons` (aperçu, export PDF et DOCX,
   atelier en `data:`, exemple), dans un fil, avec un cache par processus (empreinte du PNG,
   origine, couleur). Un pictogramme bicolore, une photo, un logo restent tels quels. Ferme
   l'entrée de `PLUS-TARD.md`.
5. **Galerie.** `documents_templates.primary_color` suit la nouvelle définition à l'ingestion
   (`gallery_traits`) ; `python -m scripts.templates refresh-colors [--dry-run]`
   (`RefreshTemplateColors`, idempotent) remesure les modèles déjà publiés — **à lancer en
   production après le déploiement**. `SelectionRead.default_primary_color` suit, donc la
   pastille « D'origine » de l'app montre la couleur du décor. Aucune migration.
6. **Vérification.** `python -m scripts.templates render-sample NOM --out DOSSIER --color …
   [--any-status]` rend l'exemple d'un modèle en PNG par couleur (Gotenberg). Vu sur
   `sacha-dubois` et `romain-dupont` (versions en revue) en origine, `#1F4FA3` et `#7B1E2B` :
   filets, frise, puces et séparateurs prennent la couleur ; nom, titres, dates et texte restent
   noirs.

### Ce qu'on a écarté

- **Garder les titres comme couleur, mais ne pas les recolorer.** Un modèle aux titres bleus
  et au décor noir aurait montré « bleu » comme couleur d'origine et changé… le noir.
  La couleur affichée doit être celle qui change.
- **Un seuil de lisibilité à 3:1 (grands textes).** Les corps d'un CV font 8 à 11 pt : 4,5:1
  partout, simple et sûr.
- **Décider « posé sur » en rendant la page.** Un rendu par changement de couleur coûterait
  400 ms ; la géométrie mesurée de la spécification suffit et reste pure.
- **Teinter les pictogrammes dans le moteur HTML/DOCX.** Les deux moteurs auraient dupliqué la
  mesure ; un point d'application (les `assets` envoyés) sert les quatre rendus.

### Conséquences

- La **lettre** (ADR-0030) lit `styles.section_title.color` comme accent de l'objet : c'est un
  texte, il ne suit plus la couleur choisie. Si la lettre doit porter la couleur, c'est par un
  décor (un filet à `primary_color_of(spec)`), à décider dans le lot « lettre ».
- `RenderRequest`, `ExportRequest`, `LetterExportRequest`, `SelectRequest` : inchangés ; le
  sens de `primary_color` dans les contrats est mis à jour (`contrat-api-documents.md` § 1,
  `contrat-api-modeles.md`).
- App : les textes de la feuille « Quelle couleur ? » disent ce qui change ; rien à recoloriser
  côté app (la WebView rend ce que le serveur renvoie).
