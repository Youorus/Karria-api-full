"""Centre d'envoi des e-mails.

    ┌────────────────────────────────────────────────────────────────┐
    │  AJOUTER UN E-MAIL                                             │
    │                                                                │
    │  1. une valeur dans `MailMessage`                              │
    │  2. une entrée dans `REGISTRY` — objet, catégorie, aperçu      │
    │  3. un fichier texte dans `templates/`                         │
    │                                                                │
    │  Rien d'autre : ni configuration, ni câblage, ni mise en page. │
    └────────────────────────────────────────────────────────────────┘

Même dispositif que le registre de buckets, et pour la même raison : ce qui
se déclare en données ne se disperse pas dans le code.

Un gabarit s'écrit en **texte**, pas en HTML :

    # Un titre
    Un paragraphe ordinaire.
    [code]$code[/code]
    ~ Une mention discrète.
    ! Un avertissement de sécurité.
    - Un élément de liste
    [button:Libellé]$url[/button]

La mise en forme — tableaux, styles en ligne, mode sombre, version texte —
ne s'écrit qu'une fois, dans `layout.py`, à partir des jetons de design du
frontend repris dans `theme.py`.
"""

from karria.shared.mail.errors import MailDeliveryError
from karria.shared.mail.factory import build_mailer
from karria.shared.mail.messages import (
    REGISTRY,
    MailCategory,
    MailMessage,
    MailTemplate,
)
from karria.shared.mail.ports import Mailer, OutgoingMail
from karria.shared.mail.rendering import RenderedMail, render, render_for

__all__ = [
    "REGISTRY",
    "MailCategory",
    "MailDeliveryError",
    "MailMessage",
    "MailTemplate",
    "Mailer",
    "OutgoingMail",
    "RenderedMail",
    "build_mailer",
    "render",
    "render_for",
]
