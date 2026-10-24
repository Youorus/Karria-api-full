"""Commandes d'exploitation des modèles de CV (module `documents`, ADR-0020).

    uv run python -m scripts.templates submit FICHIER.pdf --name meridian --label Meridian [--premium] [--inline]
    uv run python -m scripts.templates ingest VERSION_ID     # (re)lance l'analyse d'une version en attente
    uv run python -m scripts.templates list                  # modèles, versions, fidélité, verdict

`submit` dépose le PDF (bucket `templates`) et crée la version ; l'analyse part
dans la file (worker) ou, avec `--inline`, tourne dans ce processus — utile pour
amorcer un environnement et lire le verdict tout de suite. Rien ne tourne au
démarrage de l'API (AGENTS.md §6.4). Une version sous le seuil de fidélité
n'est pas publiée : elle reste `review`, avec ses mesures et ses substitutions
de polices (voir `list`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import UUID

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.documents import (
    ProcrastinateTemplateJobs,
    build_ingest_template,
    build_submit_template,
)
from karria.modules.documents.application import TemplateRepository
from karria.modules.documents.infrastructure import TortoiseTemplateRepository
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.logging import configure_logging
from karria.shared.queue import build_queue_app
from karria.shared.storage import build_object_storage


def _summary(report: dict[str, object]) -> str:
    fidelity = report.get("fidelity") or {}
    pdf = (fidelity or {}).get("pdf", {}) if isinstance(fidelity, dict) else {}
    docx = (fidelity or {}).get("docx", {}) if isinstance(fidelity, dict) else {}
    gate = report.get("gate") or {}
    return json.dumps(
        {
            "pdf": pdf.get("score") if isinstance(pdf, dict) else None,
            "pdf_components": pdf.get("components") if isinstance(pdf, dict) else None,
            "docx": docx.get("score") if isinstance(docx, dict) else None,
            "docx_components": docx.get("components") if isinstance(docx, dict) else None,
            "gate": gate,
            "fonts": [
                f"{f.get('source')} → {f.get('served')} ({f.get('origin')})"
                for f in report.get("fonts", []) or []  # type: ignore[union-attr]
                if isinstance(f, dict)
            ],
        },
        ensure_ascii=False,
    )


async def _list(templates: TemplateRepository) -> None:
    for template in await templates.list_all():
        published = template.published_version or "—"
        print(
            f"{template.name} ({template.label}) id={template.id} publiée=v{published} premium={template.is_premium}"
        )
        for version in await templates.versions_of(template.id):
            print(
                f"  v{version.version} {version.status.value} id={version.id} {version.error or ''}"
            )
            if version.report:
                print(f"    {_summary(version.report)}")


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.observability)
    await connect(TORTOISE_ORM)
    try:
        async with AsyncExitStack() as resources:
            storage = await build_object_storage(settings, resources)
            templates = TortoiseTemplateRepository()
            if args.command == "list":
                await _list(templates)
                return 0
            if args.command == "ingest":
                done = await build_ingest_template(settings, storage).execute(UUID(args.version_id))
                print(f"{done.status.value if done else 'introuvable'}")
                if done and done.report:
                    print(_summary(done.report))
                return 0
            pdf = await asyncio.to_thread(Path(args.file).read_bytes)
            queue = build_queue_app(settings.db)
            async with queue.open_async():
                submit = build_submit_template(storage, ProcrastinateTemplateJobs(queue))
                version = await submit.execute(
                    pdf,
                    name=args.name,
                    label=args.label,
                    is_premium=args.premium,
                    defer=not args.inline,
                )
            print(f"version {version.version} déposée : {version.id}")
            if args.inline:
                done = await build_ingest_template(settings, storage).execute(version.id)
                print(
                    f"{done.status.value if done else '?'} — {done.error if done and done.error else ''}"
                )
                if done and done.report:
                    print(_summary(done.report))
            return 0
    finally:
        await disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit", help="déposer le PDF d'un modèle")
    submit.add_argument("file")
    submit.add_argument("--name", required=True)
    submit.add_argument("--label")
    submit.add_argument("--premium", action="store_true")
    submit.add_argument(
        "--inline", action="store_true", help="analyser dans ce processus, sans le worker"
    )
    ingest = sub.add_parser("ingest", help="analyser une version en attente")
    ingest.add_argument("version_id")
    sub.add_parser("list", help="modèles et versions")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
