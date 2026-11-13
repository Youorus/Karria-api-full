"""Commandes d'exploitation des modèles de CV (module `documents`, ADR-0020).

    uv run python -m scripts.templates submit FICHIER.pdf --name meridian --label Meridian [--premium] [--inline] [--letter LETTRE.pdf]
    uv run python -m scripts.templates ingest VERSION_ID     # (re)lance l'analyse d'une version en attente
    uv run python -m scripts.templates list                  # modèles, versions, fidélité, verdict
    uv run python -m scripts.templates refresh-colors [--dry-run]
    uv run python -m scripts.templates render-sample NOM --out DOSSIER [--color '#1F4FA3' ...] [--any-status]
    uv run python -m scripts.templates plain-letter          # les aperçus de la lettre sobre (galerie)
    uv run python -m scripts.templates dump VERSION_ID DIR   # artefacts de revue d'une version (images, spec, rapport)

`submit` dépose le PDF (bucket `templates`) et crée la version ; l'analyse part
dans la file (worker) ou, avec `--inline`, tourne dans ce processus — utile pour
amorcer un environnement et lire le verdict tout de suite. `--letter` dépose
avec lui le PDF de la **lettre assortie** (ADR-0036) : mesurée par la même
chaîne et la même porte, elle fait rester la version entière en revue si elle
passe sous le seuil. Rien ne tourne au démarrage de l'API (AGENTS.md §6.4).
Une version sous le seuil de fidélité n'est pas publiée : elle reste `review`,
avec ses mesures et ses substitutions de polices (voir `list`).

`refresh-colors` remesure la couleur de galerie (`primary_color`) des modèles
publiés depuis leur version publiée — la couleur du **décor** depuis
l'ADR-0034 § 2026-11-06, plus celle des titres. Idempotent ; `--dry-run` montre
sans écrire.

`render-sample` rend l'exemple d'un modèle en PNG (page 1, 150 dpi, via
Gotenberg), dans sa couleur d'origine puis dans chaque `--color` : de quoi
regarder ce que la couleur change. `--any-status` prend la dernière version
même non publiée (une version en revue, pour juger la couleur avant la
publication). Ne modifie rien.

`plain-letter` télécharge les polices de la lettre sobre (Carlito 400 et 700),
la rend avec son contenu d'exemple et dépose ses aperçus (`plain-letter/v<n>/`
du bucket `templates`) : c'est la page « Lettre sobre » que la galerie montre
pour tout modèle sans lettre assortie. À lancer une fois par environnement, et
à chaque changement de `PLAIN_LETTER_VERSION`. Gotenberg doit être joignable.

`dump` écrit dans un dossier ce qu'une revue regarde : `source.png`, `rendu.png`,
`cote-a-cote.png` (source | rendu), `report.json` (fidélité, passes, jetons, durées),
`spec.json`, `labels.json` (plan compris, ADR-0037), `sample.json`.
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
    build_derive_template,
    build_ingest_template,
    build_publish_plain_letter,
    build_refresh_portrait,
    build_refresh_template_colors,
    build_submit_template,
)
from karria.modules.documents.api.dependencies import build_engine
from karria.modules.documents.application import TemplateRepository, tint_icons
from karria.modules.documents.application.use_cases import version_prefix
from karria.modules.documents.domain import parse_hex_color, primary_color_of, recolor_spec
from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.plain_letter import PLAIN_FAMILY
from karria.modules.documents.domain.view import from_sample
from karria.modules.documents.infrastructure import FontLibrary, TortoiseTemplateRepository
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.shared.config import Settings, get_settings
from karria.shared.database import connect, disconnect
from karria.shared.images import side_by_side
from karria.shared.logging import configure_logging
from karria.shared.queue import build_queue_app
from karria.shared.storage import Bucket, ObjectStorage, build_object_storage


def _summary(report: dict[str, object]) -> str:
    fidelity = report.get("fidelity") or {}
    pdf = (fidelity or {}).get("pdf", {}) if isinstance(fidelity, dict) else {}
    docx = (fidelity or {}).get("docx", {}) if isinstance(fidelity, dict) else {}
    letter = (fidelity or {}).get("letter", {}) if isinstance(fidelity, dict) else {}
    letter_docx = (fidelity or {}).get("letter_docx", {}) if isinstance(fidelity, dict) else {}
    gate = report.get("gate") or {}
    return json.dumps(
        {
            "pdf": pdf.get("score") if isinstance(pdf, dict) else None,
            "pdf_components": pdf.get("components") if isinstance(pdf, dict) else None,
            "docx": docx.get("score") if isinstance(docx, dict) else None,
            "docx_components": docx.get("components") if isinstance(docx, dict) else None,
            "letter": letter.get("score") if isinstance(letter, dict) else None,
            "letter_components": letter.get("components") if isinstance(letter, dict) else None,
            "letter_docx": letter_docx.get("score") if isinstance(letter_docx, dict) else None,
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


async def _refresh_colors(*, dry_run: bool) -> None:
    changes = await build_refresh_template_colors().execute(apply=not dry_run)
    for template, color in changes:
        print(f"{template.name}: {template.primary_color or '—'} → {color or '—'}")
    print(f"{len(changes)} modèle(s) {'à changer' if dry_run else 'mis à jour'}")


async def _render_sample(
    settings: Settings,
    storage: ObjectStorage,
    templates: TemplateRepository,
    *,
    name: str,
    colors: list[str],
    out: Path,
    any_status: bool,
) -> int:
    template = await templates.get_by_name(name)
    if template is None:
        print(f"modèle inconnu : {name}")
        return 1
    version = None
    if template.published_version_id is not None:
        version = await templates.get_version(template.published_version_id)
    if version is None and any_status:
        version = next((v for v in await templates.versions_of(template.id) if v.spec), None)
    if version is None or version.spec is None:
        print(f"{name} : aucune version publiée (essayer --any-status)")
        return 1
    spec = version.spec
    assets: dict[str, bytes] = {}
    for asset_name, key in version.assets.items():
        assets[asset_name] = await storage.get(Bucket.TEMPLATES, key)
    photo_name = (spec["header"].get("photo") or {}).get("sample_file")
    photo = assets.pop(photo_name, None) if photo_name else None
    engine = build_engine(settings, storage)
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    origin = primary_color_of(spec)
    print(
        f"{name} v{version.version} ({version.status.value}) — couleur du décor : {origin or '—'}"
    )
    for color in [None, *colors]:
        chosen = parse_hex_color(color) if color else None
        recolored = recolor_spec(spec, chosen)
        icons = tint_icons(assets, spec, chosen)
        rendered = await engine.render_pdf(
            recolored, from_sample(recolored, version.sample or {}), assets=icons, photo=photo
        )
        png, _, _ = (await engine.page_images(rendered.data))[0]
        target = out / f"{name}-{(chosen or 'origine').lstrip('#').lower()}.png"
        await asyncio.to_thread(target.write_bytes, png)
        print(f"  {chosen or 'origine'} → {target}")
    return 0


async def _dump(
    templates: TemplateRepository, storage: ObjectStorage, args: argparse.Namespace
) -> int:
    """Les artefacts de revue d'une version, dans `args.directory`."""
    version = await templates.get_version(UUID(args.version_id))
    if version is None:
        print("version introuvable")
        return 1
    source = await storage.get(Bucket.TEMPLATES, version.source_key)
    render_key = f"{version_prefix(version.template_id, version.version)}/sample_render.pdf"
    rendered = (
        await storage.get(Bucket.TEMPLATES, render_key)
        if await storage.exists(Bucket.TEMPLATES, render_key)
        else None
    )
    files: dict[str, bytes] = {}
    source_png = await asyncio.to_thread(_png, source)
    files["source.png"] = source_png
    if rendered is not None:
        render_png = await asyncio.to_thread(_png, rendered)
        files["rendu.png"] = render_png
        files["cote-a-cote.png"] = await asyncio.to_thread(side_by_side, source_png, render_png)
    for name, data in (
        ("report.json", version.report),
        ("spec.json", version.spec),
        ("labels.json", version.labels),
        ("sample.json", version.sample),
    ):
        if data is not None:
            files[name] = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    await asyncio.to_thread(_write_all, Path(args.directory), files)
    print(f"{version.status.value} v{version.version} → {args.directory}")
    return 0


def _write_all(directory: Path, files: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)


def _png(pdf: bytes) -> bytes:
    with PdfDocument(pdf) as doc:
        return doc.render_png(0, dpi=100)


async def _main(args: argparse.Namespace) -> int:  # noqa: PLR0911, PLR0912, PLR0915 - une sortie par commande
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
            if args.command == "refresh-colors":
                await _refresh_colors(dry_run=args.dry_run)
                return 0
            if args.command == "render-sample":
                return await _render_sample(
                    settings,
                    storage,
                    templates,
                    name=args.name,
                    colors=args.color,
                    out=Path(args.out),
                    any_status=args.any_status,
                )
            if args.command == "dump":
                return await _dump(templates, storage, args)
            if args.command == "portraits":
                refresh = build_refresh_portrait(settings, storage)
                names = (
                    [t.name for t in await templates.list_all() if t.published_version_id]
                    if args.all
                    else [args.name]
                )
                for name in names:
                    done = await refresh.execute(name)
                    print(
                        f"{name}: {'portrait refait, aperçus à jour' if done else 'sans photo ou rien généré'}"
                    )
                return 0
            if args.command == "derive":
                derive = build_derive_template(settings, storage)
                names = (
                    [t.name for t in await templates.list_all() if t.published_version_id]
                    if args.all
                    else [args.name]
                )
                for name in names:
                    try:
                        version, report = await derive.execute(
                            name, seed=args.seed, dry_run=args.dry_run
                        )
                    except TemplateIngestionError as exc:
                        print(f"{name}: REFUSÉ — {exc.details.get('detail') or exc}")
                        continue
                    d = report["derivation"]
                    print(
                        f"{name}: v{version.version} {'(essai à blanc)' if args.dry_run else 'publiée'} — "
                        f"ressemblance {report['distance']['ssim_tolerant']:.2f}, "
                        f"polices {d['fonts']['display']} + {d['fonts']['body']}, "
                        f"couleur {d['primary']}, miroir {d['mirrored']}"
                    )
                return 0
            if args.command == "ingest":
                done = await build_ingest_template(settings, storage).execute(UUID(args.version_id))
                print(f"{done.status.value if done else 'introuvable'}")
                if done and done.report:
                    print(_summary(done.report))
                return 0
            if args.command == "plain-letter":
                library = FontLibrary(storage)
                for weight in (400, 700):
                    served = await library.file(PLAIN_FAMILY, weight, False, download=True)
                    print(
                        f"police {PLAIN_FAMILY} {weight} : {'servie' if served else 'INTROUVABLE'}"
                    )
                meta = await build_publish_plain_letter(settings, storage).execute()
                print(json.dumps(meta, ensure_ascii=False))
                return 0
            pdf = await asyncio.to_thread(Path(args.file).read_bytes)
            letter = await asyncio.to_thread(Path(args.letter).read_bytes) if args.letter else None
            queue = build_queue_app(settings.db)
            async with queue.open_async():
                submit = build_submit_template(storage, ProcrastinateTemplateJobs(queue))
                version = await submit.execute(
                    pdf,
                    name=args.name,
                    label=args.label,
                    is_premium=args.premium,
                    defer=not args.inline,
                    letter=letter,
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
    submit.add_argument("--letter", help="le PDF de la lettre assortie (ADR-0036)")
    derive = sub.add_parser(
        "derive", help="publier une création Karria dérivée de la version publiée d'un modèle"
    )
    derive.add_argument("name", nargs="?", help="nom technique du modèle (slug)")
    derive.add_argument("--all", action="store_true", help="tous les modèles publiés")
    derive.add_argument("--seed", type=int, default=1, help="graine de la dérivation")
    derive.add_argument("--dry-run", action="store_true", help="mesurer sans publier")
    portraits = sub.add_parser(
        "portraits", help="générer le portrait d'exemple d'un modèle publié et refaire ses aperçus"
    )
    portraits.add_argument("name", nargs="?", help="nom technique du modèle (slug)")
    portraits.add_argument("--all", action="store_true", help="tous les modèles publiés")
    ingest = sub.add_parser("ingest", help="analyser une version en attente")
    ingest.add_argument("version_id")
    sub.add_parser("list", help="modèles et versions")
    refresh = sub.add_parser(
        "refresh-colors",
        help="remesurer la couleur de galerie (celle du décor) des modèles publiés",
    )
    refresh.add_argument("--dry-run", action="store_true", help="montrer sans écrire")
    render = sub.add_parser(
        "render-sample", help="rendre l'exemple d'un modèle en PNG, par couleur"
    )
    render.add_argument("name", help="nom technique du modèle (slug)")
    render.add_argument("--out", required=True, help="dossier des PNG")
    render.add_argument(
        "--color", action="append", default=[], help="une couleur #rrggbb (répétable)"
    )
    render.add_argument(
        "--any-status", action="store_true", help="la dernière version même non publiée"
    )
    sub.add_parser("plain-letter", help="rendre et déposer les aperçus de la lettre sobre")
    dump = sub.add_parser("dump", help="artefacts de revue d'une version")
    dump.add_argument("version_id")
    dump.add_argument("directory")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
