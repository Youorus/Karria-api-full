"""Écrit chaque gabarit d'e-mail en HTML local, en clair et en sombre émulé, pour les regarder.

    uv run python -m scripts.mail_preview [DOSSIER]    # sans dossier : un dossier temporaire

Un fichier par message et par thème (`<message>-clair.html`, `<message>-sombre.html`),
plus `index.html` qui les montre côte à côte à largeur de téléphone et de
bureau. Le sombre est **émulé** : la media query du gabarit est réécrite en
règles toujours actives, ce qu'un client en mode sombre ferait. Aucun
courriel ne part ; c'est l'outil de la vérification visuelle (ADR-0032).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Final

from karria.shared.i18n import Language
from karria.shared.mail import MailMessage, OutgoingMail, render_for

#: Un contexte qui remplit tous les gabarits.
CONTEXT: Final[dict[str, Any]] = {
    "code": "418293",
    "expires_in": 10,
    "first_name": "Awa",
    "subject": "Test SMTP Karria",
    "body": "La configuration SMTP de Karria fonctionne correctement.",
    "user_email": "awa@exemple.fr",
    "preview": "Le fil ne se charge plus depuis ce matin.",
    "kind_label": "texte",
    "console_url": "https://api.karria.fr/api/v1/internal/support/console",
    "sender_name": "Awa Diallo",
    "documents": "- CV - Awa Diallo.pdf\n- Lettre de motivation - Acme.pdf",
    "count": 2,
    "amount": "2,99\u00a0€",
    "coffee_label": "un café",
    "template_label": "Meridian",
}


def emulate_dark(html: str) -> str:
    """Rend actives les règles du bloc `prefers-color-scheme: dark`."""
    return html.replace("@media (prefers-color-scheme: dark){", "@media all{")


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path(tempfile.mkdtemp(prefix="karria-mails-"))
    out.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    for message in MailMessage:
        rendered = render_for(
            OutgoingMail(
                message=message, to="awa@exemple.fr", context=CONTEXT, language=Language.FR
            ),
            app_url="https://karria.fr",
        )
        light = out / f"{message.value}-clair.html"
        dark = out / f"{message.value}-sombre.html"
        light.write_text(rendered.html_body, encoding="utf-8")
        dark.write_text(emulate_dark(rendered.html_body), encoding="utf-8")
        (out / f"{message.value}.txt").write_text(rendered.text_body, encoding="utf-8")
        cards.append(
            f"<h2>{message.value} — {rendered.subject}</h2>"
            f'<div class="row"><iframe src="{light.name}" width="390" height="900"></iframe>'
            f'<iframe src="{dark.name}" width="390" height="900"></iframe>'
            f'<iframe src="{light.name}" width="700" height="900"></iframe></div>'
        )
    (out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Gabarits Karria</title>"
        "<style>body{font-family:-apple-system,sans-serif;background:#ddd;padding:20px}"
        ".row{display:flex;gap:16px;align-items:flex-start;overflow-x:auto}"
        "iframe{border:0;background:#fff;box-shadow:0 2px 12px rgba(0,0,0,.2)}</style>"
        + "".join(cards),
        encoding="utf-8",
    )
    sys.stdout.write(f"{out / 'index.html'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
