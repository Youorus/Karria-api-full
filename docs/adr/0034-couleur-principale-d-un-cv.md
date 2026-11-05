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
