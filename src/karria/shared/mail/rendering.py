"""Assemble un message : objet, HTML, et sa version texte.

Un gabarit s'écrit en texte, pas en HTML
----------------------------------------
Écrire du HTML d'e-mail à la main — tableaux imbriqués, styles en ligne —
condamnerait chaque nouveau message à recopier trente lignes de mise en
forme, et à les faire diverger. Les gabarits portent donc le **texte**, et
le rendu applique le design :

    # Un titre
    Un paragraphe ordinaire.
    [code]$code[/code]          le code, en gros et espacé
    ~ Une mention discrète.
    ! Un avertissement de sécurité.
    - Un élément de liste
    [button:Libellé]$url[/button]
    [app:Libellé]$deeplink[/app]    le lien qui ouvre l'application

Ajouter un e-mail revient donc à écrire ce qu'on veut dire. La mise en
forme, elle, ne s'écrit qu'une fois — dans `layout.py`.

Ce que le rendu ajoute de lui-même, et qu'un gabarit n'écrit donc pas :

- `$greeting` et `$name_suffix`, la salutation et le prénom, qui restent
  propres quand le prénom manque ;
- la signature « L'équipe Karria » et le pied de page, dans les deux
  versions ;
- la typographie : apostrophes courbes, espaces insécables devant
  « : ; ! ? » et à l'intérieur des guillemets français.

Pourquoi `string.Template` et pas un moteur de gabarits
-------------------------------------------------------
Jinja2 serait le choix évident sur le web. Ici, chaque message est un
texte court, sans boucle ni condition : la substitution de la bibliothèque
standard suffit, et ne rien ajouter au projet vaut mieux qu'une dépendance
de plus pour trois `$variable` (AGENTS.md §6.11).

`Template.substitute` échoue bruyamment sur une variable manquante — ce
qui est exactement ce qu'on veut : mieux vaut un envoi qui échoue en
recette qu'un courriel portant « Bonjour $first_name ».

La version texte, et pourquoi elle n'est pas facultative
--------------------------------------------------------
Un message sans alternative texte est un signal de courrier indésirable
pour la plupart des filtres. Elle sert aussi les lecteurs d'écran et les
clients configurés en texte seul.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Final

from karria.shared.i18n import DEFAULT_LANGUAGE, Language
from karria.shared.mail import theme
from karria.shared.mail.messages import REGISTRY, MailMessage
from karria.shared.mail.ports import OutgoingMail

#: Dossier des gabarits, à côté de ce fichier.
TEMPLATES_DIR: Final = Path(__file__).parent / "templates"


@dataclass(frozen=True, slots=True)
class RenderedMail:
    """Un message rendu, prêt à être remis à un transport."""

    subject: str
    html_body: str
    text_body: str


def render(
    message: MailMessage,
    context: dict[str, Any],
    language: Language = DEFAULT_LANGUAGE,
    *,
    contact_email: str = theme.CONTACT_EMAIL,
) -> RenderedMail:
    """Rend un message avec son contexte.

    Raises:
        KeyError: Une variable du gabarit manque au contexte. Volontaire :
            un courriel qui part avec un `$code` littéral est pire qu'un
            envoi qui échoue.
    """
    definition = REGISTRY[message]
    complet = _personnaliser(context, language)
    safe = {key: _escape(value) for key, value in complet.items()}

    def remplir(source: str) -> str:
        """Substitue, puis applique la typographie de la langue."""
        return _typographie(Template(source).substitute(safe), language)

    from karria.shared.mail import layout  # noqa: PLC0415 - cycle d'import

    body = "\n".join(
        (
            _compose(remplir(_read(definition.template, language))),
            layout.signature(_SIGNATURE[language]),
        )
    )
    # L'objet est un en-tête, pas du HTML : il se remplit avec les valeurs
    # **brutes**. Échappées, un prénom comme « O'Neil » arrivait en
    # « O&#x27;Neil » dans la liste des messages. Il n'est échappé qu'au
    # moment d'entrer dans le `<title>` du document.
    subject = _objet(
        _typographie(
            Template(definition.subject[language]).substitute(
                {key: str(value) for key, value in complet.items()}
            ),
            language,
        )
    )
    preview = remplir(definition.preview.get(language, ""))
    note = remplir(definition.footer_note.get(language, "")) or _NOTE_PAR_DEFAUT[language]
    destinataire = context.get("recipient")
    recipient = _escape(destinataire) if destinataire else None

    return RenderedMail(
        subject=subject,
        html_body=layout.wrap(
            title=_escape(subject),
            preview=preview,
            body=body,
            footer=layout.footer(
                recipient=recipient,
                note=note,
                lang=language.value,
                contact_email=contact_email,
            ),
            lang=language.value,
        ),
        text_body=_to_text(body, language)
        + "\n\n-- \n"
        + _pied_texte(note, recipient=recipient, language=language, contact_email=contact_email),
    )


#: La formule d'appel, par langue.
_SALUTATION: Final[dict[Language, str]] = {Language.FR: "Bonjour", Language.EN: "Hello"}

#: La signature, commune à tous les messages et ajoutée ici plutôt que
#: dans chaque gabarit : à écrire vingt-cinq fois, elle avait fini par ne
#: figurer dans aucun.
_SIGNATURE: Final[dict[Language, str]] = {
    Language.FR: "L\u2019équipe Karria",
    Language.EN: "The Karria team",
}


def _personnaliser(context: dict[str, Any], language: Language) -> dict[str, Any]:
    """Ajoute au contexte les formules qui dépendent du prénom.

    ═══ Pourquoi le prénom ne s'écrit plus directement ═══

    Un compte peut n'avoir aucun prénom — ouverture par Apple sans nom
    transmis, préinscription par simple adresse. `Bonjour $first_name,`
    donnait alors « Bonjour , » et l'objet « , votre voyage est bien
    arrivé » : exactement ce qui fait prendre un message pour un envoi
    automatique mal fait.

    Les gabarits écrivent donc `$greeting` (« Bonjour Awa, » ou
    « Bonjour, ») et `$name_suffix` (« , Awa » ou rien). Le choix se fait
    une fois, ici, et aucun gabarit ne peut le rater.

    `None` compte comme vide : `str(None)` aurait écrit « Bonjour None, ».
    """
    prenom = str(context.get("first_name") or "").strip()
    salut = _SALUTATION[language]
    return {
        **context,
        "first_name": prenom,
        "greeting": f"{salut} {prenom}," if prenom else f"{salut},",
        "name_suffix": f", {prenom}" if prenom else "",
    }


#: Espace simple devant une ponctuation haute, en français.
_AVANT_PONCTUATION: Final = re.compile(r" ([:;!?»])")
#: Apostrophe droite entre deux lettres.
_APOSTROPHE: Final = re.compile(r"(?<=\w)'(?=\w)")
#: Espace entre un nombre et le symbole de l'euro.
_AVANT_EURO: Final = re.compile(r"(?<=\d) €")


def _typographie(texte: str, language: Language) -> str:
    """Applique la typographie de la langue au texte substitué.

    ═══ Pourquoi ici et non dans les gabarits ═══

    Une espace insécable ne se voit pas dans un éditeur : écrite à la
    main, elle est oubliée une fois sur deux, et « Motif : » finit coupé
    en fin de ligne, les deux-points seuls au début de la suivante. La
    règle s'applique donc au rendu, à tous les messages à la fois.

    Elle passe **après** la substitution, donc sur les valeurs aussi : un
    motif saisi par l'équipe avec « ? » est corrigé comme le reste. Les
    valeurs sont déjà échappées — une apostrophe venue du contexte est
    devenue `&#x27;` et n'est pas touchée : on ne réécrit pas un prénom.

    L'espace insécable ordinaire (U+00A0) et non l'espace fine (U+202F) :
    la fine manque encore à certaines polices de messagerie, qui
    l'affichent en carré.
    """
    texte = _APOSTROPHE.sub("\u2019", texte)
    texte = _AVANT_EURO.sub("\u00a0€", texte)
    if language is Language.FR:
        texte = _AVANT_PONCTUATION.sub("\u00a0\\1", texte)
        texte = texte.replace("« ", "«\u00a0")
    return texte


def _objet(texte: str) -> str:
    """Nettoie un objet : jamais d'espace double ni de blanc aux bords."""
    return re.sub(r"\s{2,}", " ", texte).strip()


def render_for(
    mail: OutgoingMail, *, app_url: str, contact_email: str = theme.CONTACT_EMAIL
) -> RenderedMail:
    """Rend un envoi, en complétant son contexte.

    Trois valeurs sont ajoutées ici et **jamais par l'appelant** :
    `recipient` pour le pied de page, `app_url` pour les liens web, et
    `deeplink` pour le lien qui ouvre l'application. Les laisser à la
    charge de chaque appel garantirait qu'un jour l'un d'eux manque — et
    un `KeyError` avalé fait disparaître un e-mail en silence.

    La langue vient de l'envoi lui-même : c'est l'appelant qui sait à qui
    il s'adresse, et le rendu n'a pas à la deviner.
    """
    contexte = {"recipient": mail.to, "app_url": app_url, **mail.context}
    contexte.setdefault("deeplink", _deeplink(contexte.get("url")))
    return render(mail.message, contexte, mail.language, contact_email=contact_email)


def _deeplink(url: object) -> str:
    """Traduit un lien web en lien d'application.

    ═══ Pourquoi le dériver plutôt que le passer ═══

    Chaque appelant construit déjà l'adresse web de ce qu'il annonce —
    `…/suivi/<id>`, `…/compte/trajets`. Lui demander de fournir en plus
    la variante applicative, c'est deux chaînes à tenir d'accord, et
    celle qu'on ne regarde pas finit par pointer ailleurs.

    Le chemin est donc repris tel quel, seul le début change :
    `https://karria.fr/suivi/42` devient `karria://suivi/42`. Les
    routes de l'application portent les mêmes noms que celles du site —
    c'est une convention, et ce fichier est l'endroit où elle est
    écrite.

    Le domaine est écarté, et c'est voulu : une application n'en a pas.
    C'est aussi ce qui rend la dérivation juste en recette, où l'adresse
    du site pointe ailleurs que la production.

    À défaut de lien précis, on ouvre l'application sans destination :
    mieux vaut l'accueil que rien.
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return f"{theme.SCHEME_APP}://"
    _, _, reste = url.partition("://")
    _, _, chemin = reste.partition("/")
    return f"{theme.SCHEME_APP}://{chemin}"


def _read(name: str, language: Language) -> str:
    """Lit un gabarit dans une langue.

    Relu à chaque envoi et non mis en cache : un envoi transactionnel se
    compte en dizaines par minute, la lecture d'un fichier de deux
    kilo-octets ne pèse rien, et cela permet de corriger une formulation
    sans redémarrer.
    """
    return (TEMPLATES_DIR / language.value / f"{name}.html").read_text(encoding="utf-8")


def _escape(value: Any) -> str:
    """Échappe une valeur destinée au HTML.

    Un prénom contient des apostrophes, parfois des chevrons collés par
    inadvertance. Sans échappement, ils casseraient la mise en page — et
    une valeur venue d'une source moins sûre y injecterait du balisage.
    """
    return html.escape(str(value), quote=True)


#: Mention de pied par défaut, quand un message n'en fournit pas.
#:
#: Elle dit pourquoi ce message-ci arrive. C'est la première question de
#: quelqu'un qui ne se souvient pas de nous avoir donné son adresse, et y
#: répondre en une ligne évite le signalement en indésirable — lequel
#: coûte la délivrabilité de tous les envois suivants.
_NOTE_PAR_DEFAUT: Final[dict[Language, str]] = {
    Language.FR: (
        "Vous recevez ce message parce qu&rsquo;il concerne une action sur votre compte Karria."
    ),
    Language.EN: "You are receiving this message because it concerns your Karria account.",
}


def _pied_texte(note: str, *, recipient: str | None, language: Language, contact_email: str) -> str:
    """Le pied de page de la version texte.

    Il manquait : la version texte s'arrêtait au dernier paragraphe, sans
    dire pourquoi le message arrivait, à qui, ni comment nous joindre.
    Ce sont pourtant les lignes que cherche quelqu'un qui hésite à
    signaler le message comme indésirable.
    """
    from karria.shared.mail import layout  # noqa: PLC0415 - cycle d'import

    textes = layout.PIED[language.value]
    lignes = [
        html.unescape(_TAGS.sub("", note)),
        html.unescape(_TAGS.sub("", textes["contact"].format(contact=contact_email))),
    ]
    if recipient:
        lignes.append(html.unescape(f"{textes['destinataire']} {recipient}."))
    lignes.append(f"{layout.BRAND} — {theme.SITE_URL.removeprefix('https://')}")
    return _typographie("\n".join(lignes), language)


#: Balises dont le contenu ne doit pas apparaître dans la version texte.
_INVISIBLE: Final = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAGS: Final = re.compile(r"<[^>]+>")
_BLANKS: Final = re.compile(r"\n{3,}")


#: Un lien quelconque : son adresse et son libellé.
_LIEN: Final = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
#: La cellule de puce d'une liste, et l'ouverture de la cellule qui suit.
_PUCE: Final = re.compile(r"&bull;</td>\s*<td[^>]*>", re.S)


def _to_text(body: str, language: Language = DEFAULT_LANGUAGE) -> str:
    """Produit la version texte à partir du HTML du contenu.

    Dérivée plutôt que rédigée séparément : deux textes à maintenir en
    parallèle divergent, et c'est toujours celui qu'on ne relit pas qui
    part faux.

    Deux défauts corrigés, qui la rendaient inutilisable :

    - les **liens** perdaient leur adresse — « Suivre mon colis », sans
      rien sur quoi cliquer. Ils deviennent « Libellé : adresse », le
      lien d'application compris ;
    - chaque **puce** occupait une ligne à elle seule, séparée de son
      texte par une ligne vide. Elle devient un tiret en tête de ligne.
    """
    separateur = "\u00a0: " if language is Language.FR else ": "
    text = _INVISIBLE.sub("", body)
    text = _PUCE.sub("- ", text)
    text = _LIEN.sub(
        lambda m: (
            m.group(1) if m.group(2) == m.group(1) else f"{m.group(2)}{separateur}{m.group(1)}"
        ),
        text,
    )
    # Les fins de bloc deviennent des sauts de ligne : sans cela, tout le
    # message se retrouve sur une seule ligne interminable.
    text = re.sub(r"</(p|h1|h2|div|tr|td|table)>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = _TAGS.sub("", text)
    text = html.unescape(text)
    lignes = [ligne.strip() for ligne in text.splitlines()]
    text = _BLANKS.sub("\n\n", "\n".join(lignes)).strip()
    # Les éléments d'une liste se suivent sans ligne vide, comme dans
    # n'importe quel courriel écrit à la main.
    return re.sub(r"(?m)^(- .*)\n\n(?=- )", r"\1\n", text)


#: Marqueurs de la syntaxe des gabarits, dans l'ordre où on les cherche.
_CODE: Final = re.compile(r"^\[code\](.+?)\[/code\]$")
_BUTTON: Final = re.compile(r"^\[button:(.+?)\](.+?)\[/button\]$")
#: Le lien d'ouverture de l'application : `[app:Libellé]karria://…[/app]`.
_APP: Final = re.compile(r"^\[app:(.+?)\](.+?)\[/app\]$")

#: Mise en évidence à l'intérieur d'une ligne : `**ceci**`.
_GRAS: Final = re.compile(r"\*\*(.+?)\*\*")


def _emphase(ligne: str) -> str:
    """Rend le gras d'une ligne de gabarit.

    La substitution s'applique **après** l'échappement HTML du contexte,
    donc sur du texte déjà sûr : une valeur qui contiendrait des
    astérisques ne peut produire que du gras, jamais du balisage.
    """
    return _GRAS.sub(lambda m: f'<strong style="font-weight:700;">{m.group(1)}</strong>', ligne)


def _compose(source: str) -> str:
    """Transforme le texte d'un gabarit en HTML stylé.

    Ligne à ligne, sans état : un gabarit d'e-mail transactionnel n'a ni
    imbrication ni condition. Un analyseur plus riche introduirait des cas
    limites pour un besoin qui n'existe pas.
    """
    from karria.shared.mail import layout  # noqa: PLC0415 - cycle d'import

    rendu: list[str] = []
    puces: list[str] = []

    def vider_les_puces() -> None:
        """Referme une liste en cours, le cas échéant."""
        if puces:
            rendu.append(layout.bullet_list(puces.copy()))
            puces.clear()

    for brute in source.splitlines():
        ligne = brute.strip()
        if not ligne:
            vider_les_puces()
            continue

        if ligne.startswith("- "):
            puces.append(_emphase(ligne[2:].strip()))
            continue
        vider_les_puces()

        if ligne.startswith("# "):
            rendu.append(layout.heading(_emphase(ligne[2:].strip())))
        elif ligne.startswith("~ "):
            rendu.append(layout.muted(_emphase(ligne[2:].strip())))
        elif ligne.startswith("! "):
            rendu.append(layout.warning(_emphase(ligne[2:].strip())))
        elif match := _CODE.match(ligne):
            rendu.append(layout.code_block(match.group(1)))
        elif match := _BUTTON.match(ligne):
            rendu.append(layout.button(match.group(1), match.group(2)))
        elif match := _APP.match(ligne):
            rendu.append(layout.app_link(match.group(1), match.group(2)))
        else:
            rendu.append(layout.paragraph(_emphase(ligne)))

    vider_les_puces()
    return "\n".join(rendu)
