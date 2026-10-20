"""Le gabarit commun à tous les e-mails.

Pourquoi des tableaux et des styles en ligne
---------------------------------------------
Le HTML des messageries est resté figé au début des années 2000. Outlook
rend le HTML avec le moteur de Word : ni flexbox, ni grille, ni `var()`,
et une prise en charge partielle des marges. Gmail supprime une partie des
feuilles de style. Un e-mail moderne s'écrit donc en tableaux imbriqués,
avec chaque propriété **en ligne** — ce qui serait une faute sur le web est
ici la seule chose qui fonctionne partout.

Ce que ce gabarit garantit
--------------------------
- **600 pixels de large**, la largeur historique : au-delà, Outlook coupe.
- **Un en-tête de marque** : le symbole, puis le mot « Karria ». Les deux,
  et pas seulement l'image — un client sur deux bloque les images
  distantes par défaut, et un en-tête réduit à un rectangle vide est le
  premier signe d'un message qu'on prend pour une imitation. Le mot est du
  texte : il s'affiche toujours.
- **Un pied de page complet** : les liens contractuels, les magasins
  d'applications, l'adresse du site et le rappel du destinataire. C'est la
  partie qu'on ne lit qu'une fois — le jour où l'on se demande d'où vient
  ce message, ou ce qu'on a accepté.
- **Un texte d'aperçu masqué**, affiché par les clients à côté de l'objet.
  Sans lui, ils montrent les premiers mots du HTML.
- **Le mode sombre**, déclaré par `color-scheme` : sans lui, plusieurs
  clients inversent les couleurs eux-mêmes, et une palette chaude devient
  méconnaissable.
- **Une version texte**, produite par le même appel. Elle sert
  l'accessibilité, les clients qui refusent le HTML, et surtout la
  délivrabilité : un message sans alternative texte est un signal de
  courrier indésirable.
"""

from __future__ import annotations

from typing import Final

from karria.shared.mail import theme

#: Nom affiché de l'expéditeur, et titre du gabarit.
BRAND: Final = "Karria"

#: Pages contractuelles, dans l'ordre où elles se lisent au pied.
LIENS_LEGAUX: Final = (
    ("Conditions générales", "/cgu"),
    ("Confidentialité", "/confidentialite"),
    ("Cookies", "/cookies"),
    ("Mentions légales", "/mentions-legales"),
)

#: Les mêmes pages, nommées en anglais. Elles pointent les pages
#: françaises : ce sont les seules qui font foi.
LIENS_LEGAUX_EN: Final = (
    ("Terms", "/cgu"),
    ("Privacy", "/confidentialite"),
    ("Cookies", "/cookies"),
    ("Legal notice", "/mentions-legales"),
)

#: Les textes du pied, par langue.
#:
#: Le pied était écrit en français pour tout le monde : un anglophone
#: lisait un message en anglais qui se terminait en français, ce qui a
#: tout d'une imitation mal finie.
PIED: Final[dict[str, dict[str, str]]] = {
    "fr": {
        "contact": (
            "Une question&nbsp;? &Eacute;crivez-nous &agrave; "
            f'<a href="mailto:{{contact}}" style="color:{theme.TEXTE_PIED};'
            'text-decoration:underline;">{contact}</a> '
            "ou r&eacute;pondez simplement &agrave; ce message."
        ),
        "destinataire": "Ce message a &eacute;t&eacute; envoy&eacute; &agrave;",
        "signature": "la plateforme d&rsquo;emploi qui travaille pour vous.",
        "magasins": (
            "L&rsquo;application arrive bient&ocirc;t sur l&rsquo;App Store et Google Play."
        ),
        "app_store": "T&eacute;l&eacute;charger sur l&rsquo;App Store",
        "play_store": "Disponible sur Google Play",
    },
    "en": {
        "contact": (
            "Any question? Write to us at "
            f'<a href="mailto:{{contact}}" style="color:{theme.TEXTE_PIED};'
            'text-decoration:underline;">{contact}</a> '
            "or simply reply to this message."
        ),
        "destinataire": "This message was sent to",
        "signature": "the job platform that works for you.",
        "magasins": "The app is coming soon to the App Store and Google Play.",
        "app_store": "Download on the App Store",
        "play_store": "Get it on Google Play",
    },
}

#: Fiches des magasins d'applications, quand elles existeront.
#:
#: `None` tant que l'application n'est pas publiée, et c'est délibéré. Un
#: bouton de téléchargement qui mène à une page inexistante est pire que
#: pas de bouton : il se présente comme la promesse la plus concrète du
#: message et c'est la seule qui échoue. Tant qu'elles valent `None`, le
#: pied annonce la disponibilité à venir sans rien promettre de cliquable.
#:
#: Le jour de la publication, renseigner ces deux valeurs suffit — le
#: rendu bascule seul sur des liens. Les visuels officiels des magasins ne
#: sont volontairement pas utilisés : leurs chartes n'autorisent leur
#: emploi que pour pointer une fiche réelle.
APP_STORE_URL: Final[str | None] = None
PLAY_STORE_URL: Final[str | None] = None


def header() -> str:
    """L'en-tête de marque : le symbole, puis le mot.

    Les deux, et dans cet ordre. Le symbole est une image distante — donc
    absente chez qui bloque les images, ce que font la plupart des clients
    tant que l'expéditeur n'est pas connu. Le mot, lui, est du texte : il
    s'affiche toujours. Un en-tête réduit à un carré vide est le premier
    signe d'un message qu'on classe en indésirable sans le lire.

    L'image porte `alt="Karria"` malgré le mot juste à côté : un lecteur
    d'écran annoncerait sinon une image sans nom, et certains clients
    affichent le texte de remplacement à la place du carré manquant.
    """
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="margin:0 0 30px;">
  <tr>
    <td valign="middle" style="padding-right:11px;">
      <img src="{theme.LOGO_URL}" width="{theme.LOGO_TAILLE}" height="{theme.LOGO_TAILLE}"
           alt="{BRAND}"
           style="display:block;width:{theme.LOGO_TAILLE}px;height:{theme.LOGO_TAILLE}px;
                  border:0;outline:none;text-decoration:none;">
    </td>
    <td valign="middle"
        style="font-family:{theme.POLICE_TEXTE};font-size:20px;font-weight:700;
               letter-spacing:-0.02em;color:{theme.TEXTE};">{BRAND}</td>
  </tr>
</table>"""


def _lien_pied(label: str, href: str) -> str:
    """Un lien de pied de page.

    Souligné, et non seulement coloré : le pied est en gris clair, et un
    lien qui ne se distingue que par sa teinte y devient invisible pour qui
    perçoit mal les contrastes.
    """
    return (
        f'<a href="{href}" style="color:{theme.TEXTE_PIED};'
        f'text-decoration:underline;white-space:nowrap;">{label}</a>'
    )


def _magasins(textes: dict[str, str]) -> str:
    """La ligne des magasins d'applications.

    Deux états, selon que les fiches existent. Tant qu'elles n'existent
    pas, la ligne l'annonce sans rien rendre cliquable : une promesse de
    téléchargement qui aboutit à une page d'erreur coûte plus cher que
    l'attente qu'elle prétend abréger.
    """
    if APP_STORE_URL and PLAY_STORE_URL:
        return (
            f"{_lien_pied(textes['app_store'], APP_STORE_URL)}"
            f'<span style="padding:0 8px;">&middot;</span>'
            f"{_lien_pied(textes['play_store'], PLAY_STORE_URL)}"
        )
    return textes["magasins"]


def footer(
    *,
    recipient: str | None,
    note: str,
    lang: str = "fr",
    contact_email: str = theme.CONTACT_EMAIL,
) -> str:
    """Le pied de page commun à tous les messages.

    Cinq étages, du plus utile au plus rare :

    1. la mention propre au message — pourquoi celui-ci, précisément ;
    2. l'adresse où nous écrire. Elle manquait : seuls quelques gabarits
       la citaient, et un message sans contact visible est un message
       auquel on ne peut que répondre au hasard ou le signaler ;
    3. les pages contractuelles, qu'on va chercher le jour d'un litige ;
    4. les magasins et l'adresse du site ;
    5. le rappel du destinataire.

    Le dernier n'est pas une formalité. Quelqu'un qui possède plusieurs
    adresses doit pouvoir constater laquelle est concernée, et c'est aussi
    ce qui distingue un message légitime d'une imitation : un hameçonnage
    ignore à quelle adresse il écrit.
    """
    textes = PIED.get(lang, PIED["fr"])
    # L'adresse vient de la configuration (MAIL__FROM_ADDRESS) : la boîte
    # réellement relevée, celle où « répondre à ce message » arrive. La
    # constante du thème n'est qu'un repli.
    contact = textes["contact"].format(contact=contact_email)
    legaux = LIENS_LEGAUX_EN if lang == "en" else LIENS_LEGAUX
    liens = '<span style="padding:0 8px;">&middot;</span>'.join(
        _lien_pied(label, f"{theme.SITE_URL}{chemin}") for label, chemin in legaux
    )
    ligne_destinataire = f"<br>{textes['destinataire']} {recipient}." if recipient else ""
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td style="padding:0 0 10px;font-size:13px;line-height:1.65;
               color:{theme.TEXTE_PIED};">{note}</td>
  </tr>
  <tr>
    <td style="padding:0 0 16px;font-size:13px;line-height:1.65;
               color:{theme.TEXTE_PIED};">{contact}</td>
  </tr>
  <tr>
    <td style="padding:0 0 14px;font-size:12px;line-height:2;
               color:{theme.TEXTE_PIED};">{liens}</td>
  </tr>
  <tr>
    <td style="padding:0 0 14px;font-size:12px;line-height:1.75;
               color:{theme.TEXTE_PIED};">{_magasins(textes)}</td>
  </tr>
  <tr>
    <td style="border-top:1px solid {theme.FILET};padding:14px 0 0;
               font-size:12px;line-height:1.75;color:{theme.TEXTE_PIED};">
      {_lien_pied("karria.fr", theme.SITE_URL)}
      <span style="padding:0 8px;">&middot;</span>
      {BRAND} &mdash; {textes["signature"]}
      {ligne_destinataire}
    </td>
  </tr>
</table>"""


def wrap(*, title: str, preview: str, body: str, footer: str, lang: str = "fr") -> str:
    """Enveloppe un contenu dans le gabarit de marque.

    Args:
        title: Titre du document, lu par les lecteurs d'écran.
        preview: Texte d'aperçu, masqué visuellement.
        body: HTML du contenu, déjà stylé en ligne.
        footer: Pied de page, produit par `footer()`.
        lang: Code de langue du document. Il était écrit « fr » en dur,
            alors que la moitié des gabarits existe en anglais : un lecteur
            d'écran prononçait donc le message anglais avec la phonétique
            française.
    """
    return f"""<!doctype html>
<html lang="{lang}" style="margin:0;padding:0;">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<!-- Déclarer les deux thèmes évite qu'un client inverse lui-même la
     palette : une palette chaude passée en négatif devient illisible. -->
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:{theme.FOND};
             font-family:{theme.POLICE_TEXTE};
             -webkit-font-smoothing:antialiased;">

<!-- Aperçu : ce que le client affiche à côté de l'objet. Masqué à l'écran
     par une hauteur nulle, puis rempli d'espaces insécables pour qu'il
     n'aspire pas le début du contenu réel. -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">
  {preview}{"&#8199;&#65279;&#847;" * 60}
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{theme.FOND};padding:40px 12px;">
  <tr>
    <td align="center">

      <table role="presentation" width="{theme.LARGEUR}" cellpadding="0" cellspacing="0"
             border="0" style="width:100%;max-width:{theme.LARGEUR}px;">

        <!-- Le panneau. Il portait un filet dégradé orange en bordure
             haute ; il est parti avec le reste de l'ornement. Un e-mail
             transactionnel se reconnaît à son en-tête et à sa sobriété,
             pas à une bande de couleur — et le dégradé CSS était de
             toute façon ignoré par Outlook, qui n'en affichait que
             l'orange plat. -->
        <tr>
          <td style="background:{theme.PANNEAU};
                     border:1px solid {theme.PANNEAU_BORDURE};
                     border-radius:{theme.RAYON_PANNEAU};
                     padding:38px 40px 34px;">

            {header()}

            {body}

          </td>
        </tr>

        <tr>
          <td style="padding:24px 4px 0;">
            {footer}
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def heading(text: str) -> str:
    """Titre principal du message.

    27 px en sérif à 600 : c'était un titre de page. Un e-mail
    transactionnel n'en est pas une — il porte une information, et son
    titre doit se lire d'un coup sans prendre le pas sur la phrase qui
    l'explique. 23 px à 700, dans la police du reste, avec un
    interlettrage resserré : c'est la mesure des messages d'Apple, de
    Stripe ou d'OpenAI, et elle tient sur la largeur d'un téléphone sans
    se couper en trois lignes.
    """
    return (
        f'<h1 style="margin:0 0 16px;font-family:{theme.POLICE_TEXTE};'
        f"font-size:23px;line-height:1.3;font-weight:700;letter-spacing:-0.02em;"
        f'color:{theme.TEXTE};">{text}</h1>'
    )


def paragraph(text: str) -> str:
    """Paragraphe de texte courant."""
    return (
        f'<p style="margin:0 0 18px;font-family:{theme.POLICE_TEXTE};'
        f"font-size:15.5px;line-height:1.68;"
        f'color:{theme.TEXTE};">{text}</p>'
    )


def signature(text: str) -> str:
    """La signature, sous le dernier paragraphe de chaque message.

    Ajoutée par le rendu et non par les gabarits : un message qui finit
    sur une mention grise, sans personne pour le signer, se lit comme un
    envoi de machine — ce qu'il est, mais ce qu'il n'a pas à rappeler.
    """
    return (
        f'<p style="margin:22px 0 0;font-family:{theme.POLICE_TEXTE};'
        f"font-size:15.5px;line-height:1.68;font-weight:600;"
        f'color:{theme.TEXTE};">{text}</p>'
    )


def muted(text: str) -> str:
    """Mention secondaire — durée de validité, avertissement discret."""
    return (
        f'<p style="margin:0 0 12px;font-family:{theme.POLICE_TEXTE};'
        f"font-size:13.5px;line-height:1.65;"
        f'color:{theme.TEXTE_ATTENUE};">{text}</p>'
    )


def code_block(code: str) -> str:
    """Le code à recopier, présenté pour être lu d'un coup d'œil.

    Gros, espacé, en chiffres tabulaires : on le recopie depuis un écran
    voisin, et la moindre confusion entre deux chiffres coûte une tentative
    sur les cinq permises.

    Il est aussi **sélectionnable** — pas une image. Un code en image
    interdit le copier-coller, ne se lit pas au lecteur d'écran, et
    disparaît chez qui bloque les images distantes.
    """
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       border="0" style="margin:8px 0 22px;">
  <tr>
    <td align="center"
        style="padding:22px 16px;border-radius:{theme.RAYON_BOUTON};
               background:{theme.FOND};
               border:1px solid {theme.PANNEAU_BORDURE};">
      <span style="font-family:{theme.POLICE_TEXTE};font-size:34px;
                   font-weight:700;letter-spacing:0.32em;text-indent:0.32em;
                   color:{theme.TEXTE};">{code}</span>
    </td>
  </tr>
</table>"""


def button(label: str, href: str) -> str:
    """Bouton d'action.

    Un lien stylé en bloc, et non un `<button>` : les formulaires ne
    fonctionnent pas en messagerie, et une balise `button` n'y est pas
    cliquable.
    """
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="margin:6px 0 22px;">
  <tr>
    <td align="center" style="border-radius:{theme.RAYON_BOUTON};
                              background:{theme.PRIMAIRE};">
      <a href="{href}"
         style="display:inline-block;padding:13px 26px;font-family:{theme.POLICE_TEXTE};
                font-size:15px;font-weight:600;letter-spacing:-0.01em;color:#ffffff;
                text-decoration:none;border-radius:{theme.RAYON_BOUTON};">{label}</a>
    </td>
  </tr>
</table>"""


def app_link(label: str, href: str) -> str:
    """Le lien qui ouvre l'application, sous le bouton principal.

    ═══ Pourquoi il n'est pas le bouton ═══

    Un schéma privé — `karria://` — ne mène nulle part quand
    l'application n'est pas installée : le clic ne fait **rien**, et
    l'on croit le message cassé. Plusieurs messageries le neutralisent
    d'ailleurs, Gmail au premier rang.

    Le bouton reste donc le lien web, qui aboutit toujours. Celui-ci
    s'ajoute pour qui a l'application : un geste de moins, et l'écran
    exact plutôt que la page qui y mène.

    ═══ Ce qui le remplacera ═══

    Les Universal Links. Une fois `apple-app-site-association` servi sur
    karria.fr et `associatedDomains` déclaré dans l'application, le
    lien web ouvrira l'application tout seul — et cette seconde ligne
    n'aura plus lieu d'être.
    """
    return (
        f'<p style="margin:-8px 0 20px;font-family:{theme.POLICE_TEXTE};'
        f'font-size:13.5px;line-height:1.6;">'
        f'<a href="{href}" style="color:{theme.PRIMAIRE};'
        f'text-decoration:underline;">{label}</a></p>'
    )


def warning(text: str) -> str:
    """Avertissement de sécurité, en rouge.

    Présent sur tout message portant un code : c'est la seule ligne qui
    apprend à quelqu'un qu'un code non sollicité signale une tentative
    d'accès à son compte.
    """
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       border="0" style="margin:4px 0 0;">
  <tr>
    <td style="padding:14px 16px;border-radius:{theme.RAYON_BOUTON};
               background:#fdecec;border-left:3px solid {theme.ROUGE};
               font-size:14px;line-height:1.6;color:{theme.TEXTE};">
      {text}
    </td>
  </tr>
</table>"""


def bullet_list(items: list[str]) -> str:
    """Liste à puces.

    Une puce dessinée en cellule plutôt qu'un `<ul>` : Outlook applique ses
    propres marges aux listes, et le rendu saute d'un client à l'autre. Un
    tableau se comporte partout de la même façon.
    """
    lignes = "".join(
        f"""<tr>
    <td width="18" valign="top"
        style="padding:0 0 10px;font-size:16px;line-height:1.62;
               color:{theme.PRIMAIRE};">&bull;</td>
    <td style="padding:0 0 10px;font-size:16px;line-height:1.62;
               color:{theme.TEXTE};">{item}</td>
  </tr>"""
        for item in items
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:0 0 18px;">{lignes}</table>'
    )
