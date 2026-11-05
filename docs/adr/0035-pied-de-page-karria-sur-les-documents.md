# ADR-0035 — Le pied de page Karria sur les documents exportés

**Statut** : Proposée · **Date** : 2026-11-05

## Contexte

Un CV ou une lettre quitte l'app par le partage (AirDrop, WhatsApp, e-mail) et
arrive chez un recruteur ou un proche qui n'a jamais entendu parler de Karria.
Marc veut que chaque partage « porte Karria », comme chez les grandes
applications : que la personne qui reçoit sache d'où vient le document et puisse
installer Karria en un geste — sans que le document en souffre.

## Décision

Chaque PDF et chaque DOCX exporté (CV et lettre, variante ATS safe comprise)
porte **une ligne de pied** : « CV réalisé avec Karria · karria.fr/app »
(« Lettre réalisée avec Karria · … »), en 7 pt, gris doux (`#9a9fa6`), centrée,
le lien cliquable vers `https://karria.fr/app?utm_source=document&utm_medium=cv|letter`
(le lien intelligent du site, qui mène au bon magasin). Ni logo, ni bandeau, ni
couleur.

- **HTML/PDF** (`infrastructure/footer.py › footer_html`) : la ligne est posée en
  absolu au bas de la feuille `.page`, hors du flux — elle ne déplace aucun
  texte, ne porte pas d'ancre `data-chemin` ni la classe `.t` mesurée par le
  script d'ajustement : la pagination estimée (`estimate_pages`) et réelle ne
  changent pas. Quand la feuille prend n hauteurs de page, le bas de `.page` est
  la dernière page. L'atelier (rendu ancré) la montre aussi : ce que la personne
  voit est ce qui part.
- **DOCX** (`add_docx_footer`) : le pied de section, dans la marge basse
  (`footer_distance` 3 mm), avec un vrai `w:hyperlink` ; Word le répète sur
  chaque page — un CV tient sur une page dans l'immense majorité des cas.
- La police du pied est celle du plus petit corps du document (déjà incorporée).

## Conséquences

- Un seul module (`footer.py`) décide du texte, du lien et du style ; les quatre
  rendus l'appellent. Changer la phrase ou l'URL se fait à un endroit.
- Le site expose `karria.fr/app` (redirection par appareil, `utm_*` conservés)
  et la page publique `karria.fr/offre/<id>` ; l'app déclare les liens
  universels (`applinks:karria.fr`, App Links Android).
- Rien ne change au contrat des routes : aucun champ, aucun paramètre. Un jour,
  une option « sans pied » pour un abonnement payant serait un champ d'export —
  pas prévu aujourd'hui.
