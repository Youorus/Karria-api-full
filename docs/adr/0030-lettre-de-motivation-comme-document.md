# ADR-0030 — La lettre de motivation est un document du module `documents`, rendue dans la grammaire du modèle de CV

**Statut :** Proposée — 2026-11-05 (écrite par Fable, à valider par Marc)
**Complète :** ADR-0020, ADR-0029

## Le problème

Un dossier de candidature, c'est un CV **et** une lettre. La V1 rendait la
lettre par un gabarit HTML/CSS à part (`cover_letter_templates`), sans lien
typographique avec le CV : deux documents qui ne se ressemblaient pas. Il faut
que la paire se lise comme un dossier, et que la lettre ait les mêmes sorties
que le CV (HTML ancré, PDF, DOCX, export listé, envoi par e-mail).

## La décision

1. **Pas de modèle de lettre.** Une lettre se rend **dans la spécification du
   modèle de CV choisi** : même page, mêmes marges (la colonne de texte du
   modèle), même famille et même corps pour le texte courant, la couleur
   d'accent des titres de rubrique pour l'objet, le style du nom pour la
   signature. Rien n'est déclaré : tout est lu dans la spécification, comme
   l'intitulé synthétisé (ADR-0020 § 3.1).
2. **La lettre est une donnée structurée**, pas un texte libre :
   `{ recipient: {company, logo_url?, city?}, subject, paragraphs: [{kind,
   text}], closing, sender }`, où `kind` ∈ `interet_entreprise`,
   `interet_poste`, `competences`, `pourquoi_moi`, `libre`. Les bonnes
   pratiques RH françaises sont des **règles de domaine** (`domain/letter.py`)
   : une page, trois à quatre paragraphes (avertissement en dessous de deux ou
   au-delà de cinq), objet explicite (« Candidature au poste de … » proposé
   si l'objet ne commence pas par « Candidature »), lieu et date, formule de
   politesse sobre, signature.
3. **Le logo de l'entreprise** n'apparaît que si `logo_url` est fourni et
   joignable **sur notre hôte de stockage** (le bucket public des logos,
   ADR-0021) : il est téléchargé par l'API (jamais par Chromium ni par l'app),
   borné à 512 Ko, converti en PNG pour le DOCX. Absent ou injoignable : rien,
   sans blanc, et un avertissement `logo_unavailable`.
4. **Une vue commune aux deux moteurs** (`view["kind"] == "letter"`), comme
   pour le CV : `HtmlRenderer`/`DocxRenderer` ne changent pas, un second
   couple `LetterHtmlRenderer`/`LetterDocxRenderer` lit la même vue. Le DOCX
   porte les polices incorporées par le même mécanisme.
5. **Un export de lettre est un `GeneratedCv` de `kind: "letter"`** — même
   table, même liste, mêmes URL signées, mêmes suppressions. Les deux genres
   portent un `dossier_id` facultatif (UUID libre posé par le module
   `applications`) pour retrouver les pièces d'un dossier. Pas de score ATS
   sur une lettre (`ats: {}`) : ce que les ATS lisent d'une lettre n'est pas
   mesuré, et on ne publie pas un chiffre non mesuré.

## Conséquences

- La table `documents_generated_cvs` gagne `kind`, `dossier_id`,
  `company_name` (migration 17). Le nom du fichier téléchargé suit le genre :
  « CV - Prénom Nom.pdf », « Lettre de motivation - Entreprise.pdf ».
- Le rendu de lettre partage la route `render` (`letter` au lieu de `cv`) et
  gagne sa route d'export `POST /documents/letters/export`.
- Un modèle Premium non possédé ne rend pas de lettre non plus.
