"""Lance le collecteur d'offres (application Dokploy à part, même image que l'API).

    uv run python -m scripts.collector          # en boucle
    uv run python -m scripts.collector --once   # un passage

Équivalent de `python -m karria.collector` ; présent ici pour que les trois
processus du projet se lancent de la même façon (`scripts.worker`,
`scripts.collector`, l'API). Voir `docs/collecteur.md`.
"""

from __future__ import annotations

import asyncio
import sys

from karria.collector.__main__ import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
