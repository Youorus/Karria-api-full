"""Dépose les ressources publiques de Karria dans le bucket `assets` (ADR-0029, ADR-0032).

    uv run python -m scripts.assets brand DOSSIER     # les deux mot-logos PNG des e-mails
    uv run python -m scripts.assets put FICHIER CLE   # un fichier quelconque, type deviné

`brand` attend le dossier `src/assets/branding` de l'app (`karria-mot-logo-clair.png`,
`karria-mot-logo-sombre.png`) et les range sous `brand/karria-mot-logo-noir.png`
(le logo **noir**, pour le thème clair) et `brand/karria-mot-logo-blanc.png`
(le logo **blanc**, pour le sombre) — les noms que `shared/mail/theme.py`
attend. Idempotent : un fichier identique n'est pas réécrit. Le bucket doit
exister (`make storage-provision`).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import mimetypes
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Final

from karria.shared.config import get_settings
from karria.shared.logging import configure_logging, get_logger
from karria.shared.storage import Bucket, ObjectNotFoundError, ObjectStorage, build_object_storage

logger = get_logger("scripts.assets")

#: Fichier de l'app → clé publique. L'app nomme par **thème d'affichage**
#: (« clair » = le logo qui va sur un fond clair, donc noir) ; la clé nomme
#: la couleur du logo, ce qui ne prête pas à confusion dans un gabarit.
BRAND_FILES: Final[dict[str, str]] = {
    "karria-mot-logo-clair.png": "brand/karria-mot-logo-noir.png",
    "karria-mot-logo-sombre.png": "brand/karria-mot-logo-blanc.png",
}


async def put(storage: ObjectStorage, path: Path, key: str, content_type: str | None) -> str:
    """Écrit le fichier s'il diffère de l'objet en place ; rend l'adresse publique."""
    data = await asyncio.to_thread(path.read_bytes)
    kind = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        same = (
            hashlib.sha256(await storage.get(Bucket.ASSETS, key)).digest()
            == hashlib.sha256(data).digest()
        )
    except ObjectNotFoundError:
        same = False
    if not same:
        await storage.put(Bucket.ASSETS, key, data, content_type=kind)
        logger.info("assets.uploaded", key=key, bytes=len(data))
    else:
        logger.info("assets.unchanged", key=key)
    return storage.public_url(Bucket.ASSETS, key)


def parser() -> argparse.ArgumentParser:
    """Les deux commandes."""
    top = argparse.ArgumentParser(prog="scripts.assets", description=__doc__)
    sub = top.add_subparsers(dest="command", required=True)
    brand = sub.add_parser("brand", help="dépose les mot-logos des e-mails")
    brand.add_argument("folder", type=Path, help="src/assets/branding de l'app")
    one = sub.add_parser("put", help="dépose un fichier sous une clé")
    one.add_argument("file", type=Path)
    one.add_argument("key")
    return top


async def run(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.observability)
    async with AsyncExitStack() as resources:
        storage = await build_object_storage(settings, resources)
        if args.command == "brand":
            for name, key in BRAND_FILES.items():
                sys.stdout.write(f"{await put(storage, args.folder / name, key, 'image/png')}\n")
        else:
            sys.stdout.write(f"{await put(storage, args.file, args.key, None)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(sys.argv[1:])))
