"""Le gabarit commun à tous les e-mails (ADR-0032).

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
- **Largeur fluide, 600 pixels au plus** : la largeur historique des
  messageries ; en dessous, le panneau suit l'écran du téléphone.
- **Un en-tête de marque** : le mot-logo en image, puis le mot « Karria »
  en texte. Les deux — un client sur deux bloque les images distantes par
  défaut, et un en-tête réduit à un rectangle vide est le premier signe
  d'un message qu'on prend pour une imitation. Le mot est du texte : il
  s'affiche toujours. Deux images sont posées, l'une pour le clair, l'autre
  pour le sombre ; la feuille de style montre celle du thème.
- **Aucune étiquette, pastille, encart ni icône** : un avertissement est une
  phrase, un code est un grand nombre. C'est la grammaire des messages
  d'OpenAI, Stripe ou Linear, et c'est ce que Marc a demandé.
- **Le mode sombre, réellement** : `color-scheme` déclaré, et un bloc
  `@media (prefers-color-scheme: dark)` qui recolore fond, panneau, texte
  et liens par classes, doublé des sélecteurs `[data-ogsc]` d'Outlook. Les
  couleurs claires sont choisies pour rester lisibles **inversées**, parce
  que Gmail sur iPhone ignore la media query et inverse lui-même.
- **Un pied de page complet** : pourquoi ce message, où écrire, les pages
  contractuelles, les magasins, l'adresse du site et le rappel du
  destinataire — la partie qu'on ne lit qu'une fois, le jour où l'on se
  demande d'où vient ce message.
- **Un texte d'aperçu masqué**, affiché par les clients à côté de l'objet.
- **Une version texte**, produite par le même appel (délivrabilité).
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
PIED: Final[dict[str, dict[str, str]]] = {
    "fr": {
        "contact": (
            "Une question&nbsp;? &Eacute;crivez-nous &agrave; "
            f'<a href="mailto:{{contact}}" class="lnk" style="color:{theme.TEXTE_PIED};'
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
            f'<a href="mailto:{{contact}}" class="lnk" style="color:{theme.TEXTE_PIED};'
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
#: `None` tant que l'application n'est pas publiée : un bouton de
#: téléchargement qui mène à une page inexistante est pire que pas de
#: bouton. Le jour de la publication, renseigner ces deux valeurs suffit.
APP_STORE_URL: Final[str | None] = None
PLAY_STORE_URL: Final[str | None] = None


def dark_mode_css() -> str:
    """La feuille de style du thème sombre.

    Par **classes** (`bg`, `panel`, `txt`, `mut`, `lnk`, `btn`, `logo-light`,
    `logo-dark`) et `!important` : les styles en ligne gagnent sinon. Les
    mêmes règles sont répétées sous `[data-ogsc]`/`[data-ogsb]`, les
    marqueurs qu'Outlook pose quand il passe en sombre. Gmail sur iPhone et
    Android n'applique ni l'un ni l'autre et inverse les couleurs lui-même :
    la palette claire est faite pour rester lisible une fois inversée.
    """
    rules = (
        f".bg{{background:{theme.SOMBRE_FOND}!important}}"
        f".panel{{background:{theme.SOMBRE_PANNEAU}!important;border-color:{theme.SOMBRE_BORDURE}!important}}"
        f".txt{{color:{theme.SOMBRE_TEXTE}!important}}"
        f".mut{{color:{theme.SOMBRE_TEXTE_ATTENUE}!important}}"
        f".lnk{{color:{theme.SOMBRE_TEXTE_ATTENUE}!important}}"
        f".acc{{color:{theme.SOMBRE_PRIMAIRE}!important}}"
        f".rule{{border-color:{theme.SOMBRE_BORDURE}!important}}"
        ".logo-light{display:none!important}"
        ".logo-dark{display:block!important;max-height:none!important;max-width:none!important}"
    )
    outlook = "".join(
        f"[data-ogsc] {rule}" for rule in rules.replace("}", "}\n").splitlines() if rule
    ) + "".join(
        f"[data-ogsb] {rule}"
        for rule in rules.replace("}", "}\n").splitlines()
        if rule.startswith((".bg", ".panel"))
    )
    return f"@media (prefers-color-scheme: dark){{{rules}}}{outlook}"


def header() -> str:
    """L'en-tête de marque : le mot-logo en image, puis le mot en texte, discret.

    Deux images, l'une claire (logo noir sur fond transparent), l'autre
    sombre (logo blanc), posées côte à côte ; la feuille de style sombre
    cache la première et montre la seconde. La seconde est aussi masquée par
    `display:none` en ligne **et** réduite à zéro (`max-height:0`) pour les
    clients qui ignorent `display` sur une image. Le mot en texte, petit et
    gris, reste la garantie quand les images sont bloquées — et ne fait pas
    doublon criard quand elles s'affichent.
    """
    size = theme.LOGO_HAUTEUR
    width = theme.LOGO_LARGEUR
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="margin:0 0 26px;">
  <tr>
    <td valign="middle" style="padding-right:10px;">
      <img src="{theme.LOGO_CLAIR_URL}" width="{width}" height="{size}" alt="{BRAND}"
           class="logo-light"
           style="display:block;width:{width}px;height:{size}px;border:0;outline:none;
                  text-decoration:none;">
      <img src="{theme.LOGO_SOMBRE_URL}" width="{width}" height="{size}" alt=""
           class="logo-dark"
           style="display:none;max-height:0;max-width:0;overflow:hidden;width:{width}px;
                  height:{size}px;border:0;outline:none;text-decoration:none;">
    </td>
    <td valign="middle" class="mut"
        style="font-family:{theme.POLICE_TEXTE};font-size:13px;font-weight:500;
               letter-spacing:-0.01em;color:{theme.TEXTE_PIED};padding-top:2px;">{BRAND}</td>
  </tr>
</table>"""


def _lien_pied(label: str, href: str) -> str:
    """Un lien de pied de page, souligné : le pied est gris, la teinte seule ne suffirait pas."""
    return (
        f'<a href="{href}" class="lnk" style="color:{theme.TEXTE_PIED};'
        f'text-decoration:underline;white-space:nowrap;">{label}</a>'
    )


def _magasins(textes: dict[str, str]) -> str:
    """La ligne des magasins d'applications : cliquable seulement quand les fiches existent."""
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

    Cinq étages, du plus utile au plus rare : la mention propre au message,
    l'adresse où nous écrire, les pages contractuelles, les magasins et le
    site, le rappel du destinataire — ce qui distingue un message légitime
    d'une imitation, qui ignore à quelle adresse elle écrit.
    """
    textes = PIED.get(lang, PIED["fr"])
    contact = textes["contact"].format(contact=contact_email)
    legaux = LIENS_LEGAUX_EN if lang == "en" else LIENS_LEGAUX
    liens = '<span style="padding:0 8px;">&middot;</span>'.join(
        _lien_pied(label, f"{theme.SITE_URL}{chemin}") for label, chemin in legaux
    )
    ligne_destinataire = f"<br>{textes['destinataire']} {recipient}." if recipient else ""
    cell = (
        f'style="padding:0 0 10px;font-family:{theme.POLICE_TEXTE};font-size:13px;'
        f'line-height:1.65;color:{theme.TEXTE_PIED};"'
    )
    small = (
        f'style="padding:0 0 12px;font-family:{theme.POLICE_TEXTE};font-size:12px;'
        f'line-height:1.9;color:{theme.TEXTE_PIED};"'
    )
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td class="lnk" {cell}>{note}</td>
  </tr>
  <tr>
    <td class="lnk" {cell}>{contact}</td>
  </tr>
  <tr>
    <td class="lnk" {small}>{liens}</td>
  </tr>
  <tr>
    <td class="lnk" {small}>{_magasins(textes)}</td>
  </tr>
  <tr>
    <td class="lnk rule" style="border-top:1px solid {theme.FILET};padding:14px 0 0;
               font-family:{theme.POLICE_TEXTE};font-size:12px;line-height:1.75;
               color:{theme.TEXTE_PIED};">
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
        lang: Code de langue du document, pour les lecteurs d'écran.
    """
    return f"""<!doctype html>
<html lang="{lang}" style="margin:0;padding:0;">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<!-- Les deux thèmes sont déclarés ET stylés : un client qui ne trouve que la
     déclaration inverse la palette lui-même, à sa façon. -->
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{title}</title>
<style>
:root{{color-scheme:light dark;supported-color-schemes:light dark}}
{dark_mode_css()}
@media only screen and (max-width:480px){{
  .outer{{padding:16px 8px!important}}
  .panel{{padding:26px 20px 24px!important;border-radius:12px!important}}
}}
</style>
</head>
<body class="bg" style="margin:0;padding:0;background:{theme.FOND};
             font-family:{theme.POLICE_TEXTE};
             -webkit-font-smoothing:antialiased;word-break:break-word;">

<!-- Aperçu : ce que le client affiche à côté de l'objet. Masqué à l'écran,
     puis rempli d'espaces insécables pour qu'il n'aspire pas le contenu. -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">
  {preview}{"&#8199;&#65279;&#847;" * 60}
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       class="bg outer" style="background:{theme.FOND};padding:36px 12px;">
  <tr>
    <td align="center">

      <table role="presentation" width="{theme.LARGEUR}" cellpadding="0" cellspacing="0"
             border="0" style="width:100%;max-width:{theme.LARGEUR}px;">

        <tr>
          <td class="panel" style="background:{theme.PANNEAU};
                     border:1px solid {theme.PANNEAU_BORDURE};
                     border-radius:{theme.RAYON_PANNEAU};
                     padding:36px 40px 32px;">

            {header()}

            {body}

          </td>
        </tr>

        <tr>
          <td style="padding:22px 6px 0;">
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
    """Titre principal : court, en gras, dans la police du reste.

    23 px à 700, interlettrage resserré : la mesure des messages d'Apple,
    Stripe ou OpenAI, qui tient sur la largeur d'un téléphone.
    """
    return (
        f'<h1 class="txt" style="margin:0 0 16px;font-family:{theme.POLICE_TEXTE};'
        f"font-size:23px;line-height:1.3;font-weight:700;letter-spacing:-0.02em;"
        f'color:{theme.TEXTE};">{text}</h1>'
    )


def paragraph(text: str) -> str:
    """Paragraphe de texte courant."""
    return (
        f'<p class="txt" style="margin:0 0 18px;font-family:{theme.POLICE_TEXTE};'
        f"font-size:15.5px;line-height:1.68;"
        f'color:{theme.TEXTE};">{text}</p>'
    )


def signature(text: str) -> str:
    """La signature, sous le dernier paragraphe : l'équipe, ou la personne qui transmet."""
    return (
        f'<p class="txt" style="margin:22px 0 0;font-family:{theme.POLICE_TEXTE};'
        f"font-size:15.5px;line-height:1.68;font-weight:600;"
        f'color:{theme.TEXTE};">{text}</p>'
    )


def muted(text: str) -> str:
    """Mention secondaire — durée de validité, précision discrète. Du texte, rien d'autre."""
    return (
        f'<p class="mut" style="margin:0 0 12px;font-family:{theme.POLICE_TEXTE};'
        f"font-size:13.5px;line-height:1.65;"
        f'color:{theme.TEXTE_ATTENUE};">{text}</p>'
    )


def code_block(code: str) -> str:
    """Le code à recopier : un grand nombre espacé, sur la page, sans boîte.

    Sélectionnable — pas une image — en chiffres tabulaires, assez gros pour
    se lire depuis un écran voisin. L'encart bordé qui l'entourait est parti
    avec les autres étiquettes (ADR-0032) : le code se suffit.
    """
    return (
        f'<p class="txt" style="margin:10px 0 22px;font-family:{theme.POLICE_TEXTE};'
        f"font-size:36px;line-height:1.2;font-weight:700;letter-spacing:0.28em;"
        f'font-variant-numeric:tabular-nums;color:{theme.TEXTE};">{code}</p>'
    )


def button(label: str, href: str) -> str:
    """Bouton d'action : un lien stylé en bloc (les formulaires n'existent pas en messagerie)."""
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="margin:6px 0 22px;">
  <tr>
    <td align="center" style="border-radius:{theme.RAYON_BOUTON};
                              background:{theme.PRIMAIRE};">
      <a href="{href}"
         style="display:inline-block;padding:13px 24px;font-family:{theme.POLICE_TEXTE};
                font-size:15px;font-weight:600;letter-spacing:-0.01em;color:#ffffff;
                text-decoration:none;border-radius:{theme.RAYON_BOUTON};" class="btn">{label}</a>
    </td>
  </tr>
</table>"""


def app_link(label: str, href: str) -> str:
    """Le lien qui ouvre l'application, sous le bouton principal.

    Un schéma privé (`karria://`) ne mène nulle part quand l'application
    n'est pas installée, et plusieurs messageries le neutralisent : le
    bouton reste le lien web, celui-ci s'ajoute pour qui a l'application.
    Les Universal Links le rendront inutile.
    """
    return (
        f'<p style="margin:-8px 0 20px;font-family:{theme.POLICE_TEXTE};'
        f'font-size:13.5px;line-height:1.6;">'
        f'<a href="{href}" class="acc" style="color:{theme.PRIMAIRE};'
        f'text-decoration:underline;">{label}</a></p>'
    )


def warning(text: str) -> str:
    """Avertissement de sécurité : une phrase, en texte courant, rien de plus.

    C'était un encart à barre rouge — l'étiquette que Marc ne veut plus
    (ADR-0032). La phrase dit la même chose ; l'œil ne la confond plus avec
    une alerte de machine.
    """
    return (
        f'<p class="mut" style="margin:0 0 12px;font-family:{theme.POLICE_TEXTE};'
        f"font-size:13.5px;line-height:1.65;"
        f'color:{theme.TEXTE_ATTENUE};">{text}</p>'
    )


def bullet_list(items: list[str]) -> str:
    """Liste à puces en cellules : Outlook applique ses propres marges aux `<ul>`."""
    lignes = "".join(
        f"""<tr>
    <td width="18" valign="top" class="txt"
        style="padding:0 0 10px;font-family:{theme.POLICE_TEXTE};font-size:15.5px;
               line-height:1.68;color:{theme.TEXTE};">&bull;</td>
    <td class="txt" style="padding:0 0 10px;font-family:{theme.POLICE_TEXTE};font-size:15.5px;
               line-height:1.68;color:{theme.TEXTE};">{item}</td>
  </tr>"""
        for item in items
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:0 0 18px;">{lignes}</table>'
    )
