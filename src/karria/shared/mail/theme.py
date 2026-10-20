"""Jetons de design de Karria, transposés pour l'e-mail.

Pourquoi les recopier ici plutôt que les importer
--------------------------------------------------
Ils vivent dans `src/theme/theme.ts` de l'application mobile (`karria_app`),
héritée des jetons de la V1. Un client de messagerie ne sait pas les
lire : Outlook ignore `var()`, et Gmail supprime une partie des feuilles
de style. Toute couleur doit donc être écrite **en dur, en ligne**, dans
le HTML envoyé.

La duplication est réelle et assumée. Elle est bornée à ce fichier. Les
valeurs sont celles du **thème clair** de l'application : un e-mail se
lit sur fond clair chez la majorité des clients, et `color-scheme` laisse
ceux qui préfèrent le sombre l'adapter eux-mêmes.

Ce que l'e-mail impose, et que le web ignore
--------------------------------------------
Pas de variables, pas de flexbox, pas de grille : des **tableaux**. Pas de
polices distantes non plus — on décrit une pile de polices système, et
chaque destinataire reçoit la première qu'il possède.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------
# Palette — reprise de `theme.ts` (thème clair)
# ---------------------------------------------------------------------

#: Le bleu de la marque (`primary`). Boutons, accents, liens.
PRIMAIRE: Final = "#1877F2"
#: Le vert des bonnes nouvelles (`green`). Une offre très alignée.
VERT: Final = "#22C55E"
#: Le rouge d'alerte (`red`). Erreurs et avertissements.
ROUGE: Final = "#EF4444"
#: L'orange d'attention (`orange`). Une action attendue.
ORANGE: Final = "#F97316"
#: Le texte courant (`label`).
TEXTE: Final = "#0F172A"
#: Le fond de page (`background`).
FOND: Final = "#F6F8FC"

#: Fond du panneau, où vit le contenu (`card` / `backgroundElevated`).
#: Blanc franc : les grands expéditeurs — Apple, Stripe, Google — posent
#: tous leur contenu sur du blanc, quelle que soit la couleur de leur
#: marque.
PANNEAU: Final = "#ffffff"
#: Bordure du panneau. Exprimée en hexadécimal et non en `rgb()/alpha`
#: (`cardBorder` est à 7 % d'opacité dans l'application) : la
#: transparence est mal rendue par plusieurs clients.
PANNEAU_BORDURE: Final = "#e6e9f0"
#: Texte secondaire — mentions, pied de page (`labelSecondary`, aplati).
TEXTE_ATTENUE: Final = "#5b6475"
#: Texte du pied de page (`labelTertiary`, aplati). Plus clair que
#: `TEXTE_ATTENUE` : le pied ne se lit qu'en cas de besoin et ne doit pas
#: concurrencer le message.
TEXTE_PIED: Final = "#8a92a3"
#: Filet de séparation, à l'intérieur du panneau.
FILET: Final = "#eceff4"
#: Fond discret d'un encart (`systemFill`, aplati sur blanc).
ENCART: Final = "#eef3fc"

# ---------------------------------------------------------------------
# Typographie
# ---------------------------------------------------------------------

#: Pile de polices système.
#:
#: L'application ne charge aucune police : elle emploie celle du
#: téléphone. L'e-mail fait de même — aucune messagerie ne charge une
#: police distante de façon fiable, et une police manquante retombe sur
#: du Times si on ne nomme pas la suite.
POLICE_TEXTE: Final = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
)

#: Les titres partagent la pile du texte, en plus gras et plus serré —
#: c'est le registre du `largeTitle` de l'application (800, -0.8).
POLICE_TITRE: Final = POLICE_TEXTE

# ---------------------------------------------------------------------
# Formes
# ---------------------------------------------------------------------

#: Rayon du panneau. L'application arrondit ses cartes à 24 px ; en
#: messagerie, où le panneau fait 600 px de large, la même valeur paraît
#: molle. 14 px est la mesure de la plupart des e-mails transactionnels
#: soignés.
RAYON_PANNEAU: Final = "14px"
#: Rayon des boutons et pastilles (14 px dans l'application, 10 ici pour
#: la même raison d'échelle).
RAYON_BOUTON: Final = "10px"

#: Largeur du corps. 600 px est la largeur historique des clients de
#: messagerie : au-delà, Outlook coupe, et les webmails ajoutent une barre
#: de défilement horizontale.
LARGEUR: Final = 600


# ---------------------------------------------------------------------
# Le logo, et l'adresse du site public
# ---------------------------------------------------------------------

#: Le symbole de la marque, en PNG.
#:
#: En PNG et non en SVG : Gmail supprime purement et simplement les images
#: SVG, et Outlook ne les rend pas. Servi en 512 px pour un affichage à
#: 34 px, ce qui le garde net sur un écran à densité double.
#:
#: L'URL est absolue et pointe le site public. Une image d'e-mail ne peut
#: pas être relative — le message est lu hors de tout site. **À confirmer
#: le jour du premier envoi réel** : le fichier doit exister à cette
#: adresse, sans quoi l'en-tête n'affiche qu'un carré vide chez qui
#: autorise les images.
LOGO_URL: Final = "https://karria.fr/icon-512.png"
#: Taille d'affichage du symbole dans l'en-tête.
LOGO_TAILLE: Final = 34

#: Le site public, racine des liens du pied de page.
#:
#: Constante et non configuration : les conditions générales d'un e-mail
#: parti d'un environnement de recette restent les vraies conditions
#: générales. Les faire pointer sur `localhost` ne voudrait rien dire, et
#: `MailSettings.app_url` sert à autre chose — il vise l'application, pour
#: les liens profonds.
SITE_URL: Final = "https://karria.fr"

#: L'adresse où nous écrire, rappelée au pied de chaque message.
#:
#: Repli seulement : l'adresse réellement citée est celle de la
#: configuration (`MAIL__FROM_ADDRESS`), la boîte où « répondre à ce
#: message » arrive.
CONTACT_EMAIL: Final = "contact@karria.fr"


# ---------------------------------------------------------------------
# L'application
# ---------------------------------------------------------------------

#: Le schéma d'URL de l'application mobile, déclaré dans son `app.json`.
#:
#: `karria://offres/<id>` ouvre l'écran directement, sans passer par le
#: navigateur. C'est un **complément** au lien web, jamais un
#: remplacement : un schéma privé ne mène nulle part si l'application
#: n'est pas installée, et plusieurs messageries le neutralisent — un
#: bouton unique qui ne ferait rien vaut moins qu'un bouton qui ouvre le
#: site.
#:
#: Le jour où les Universal Links seront configurés — un fichier
#: `apple-app-site-association` sur karria.fr et `associatedDomains`
#: dans l'application — le lien web ouvrira l'application de lui-même,
#: et cette ligne deviendra inutile. Elle est le pont d'ici là.
SCHEME_APP: Final = "karria"
