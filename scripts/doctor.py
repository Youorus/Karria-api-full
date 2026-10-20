"""Vérifie que l'environnement est réellement opérationnel.

    make doctor

Répond à une seule question : **est-ce que ça marche ?** — et si non, où
exactement. Chaque contrôle dit ce qu'il vérifie, ce qu'il a trouvé, et le
geste à faire pour corriger.

Pourquoi ce script existe
-------------------------
Un `docker compose up` réussi ne prouve rien : la base peut tourner sans
migration appliquée, MinIO sans bucket, l'API sans poivre de jetons. Chacun
de ces manques produit une panne **à l'usage**, chez un utilisateur, et non
au démarrage. Ce script les fait remonter en dix secondes.

Il sert aussi de garde-fou avant un déploiement : lancé avec la
configuration de production, il refuse tout ce que l'invariant de
configuration refuserait, mais **avant** de couper le service.

Chaque fournisseur branché plus tard — modèle de langage, source
d'offres — ajoute ici son contrôle, dans le commit qui le branche.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Final

from tortoise import Tortoise, connections

from karria.bootstrap.modules import ENABLED_MODULES, all_model_modules, all_permissions
from karria.shared.config import Environment, MailProvider, Settings, get_settings
from karria.shared.database import build_tortoise_config
from karria.shared.mail import build_mailer
from karria.shared.storage import REGISTRY, Bucket, S3ObjectStorage

#: Symboles du rapport. Volontairement lisibles dans un terminal sans
#: couleur — un journal de CI n'en a pas.
OK: Final = "  OK  "
KO: Final = " ÉCHEC"
WARN: Final = " ALERTE"

#: Longueur en deçà de laquelle une clé secrète n'en est pas une.
#: 32 caractères, soit ce que produit `token_urlsafe(24)` au minimum.
MIN_SECRET_LENGTH: Final = 32


@dataclass(slots=True)
class Check:
    """Résultat d'un contrôle."""

    label: str
    passed: bool
    detail: str
    fix: str = ""
    fatal: bool = True

    def render(self) -> str:
        """Ligne de rapport."""
        mark = OK if self.passed else (KO if self.fatal else WARN)
        line = f"[{mark}] {self.label} — {self.detail}"
        if not self.passed and self.fix:
            line += f"\n         → {self.fix}"
        return line


async def _check_database(settings: Settings) -> list[Check]:
    """La base répond-elle, et le schéma est-il à jour ?"""
    checks: list[Check] = []
    try:
        await Tortoise.init(config=build_tortoise_config(settings.db, all_model_modules()))
    except Exception as exc:
        return [
            Check(
                "Base de données",
                passed=False,
                detail=f"injoignable ({type(exc).__name__})",
                fix="make db-up",
            )
        ]

    try:
        connection = connections.get("default")
        await connection.execute_query("SELECT 1")
        checks.append(Check("Base de données", passed=True, detail="joignable"))

        rows = await connection.execute_query_dict(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        tables = {row["tablename"] for row in rows}
        # Les tables attendues sont déduites des modules activés, jamais
        # écrites en dur : ajouter un module ne doit pas périmer ce script.
        missing = _missing_tables(tables)
        checks.append(
            Check(
                "Migrations",
                passed=not missing,
                detail=f"{len(tables)} tables"
                + (f", manquantes : {', '.join(sorted(missing))}" if missing else ""),
                fix="make migrate-apply",
            )
        )
    finally:
        await connections.close_all()
    return checks


def _missing_tables(present: set[str]) -> set[str]:
    """Tables attendues mais absentes.

    Le préfixe d'un module suffit : chaque module nomme ses tables d'après
    lui. On vérifie donc qu'au moins une table existe par module ayant des
    modèles — assez pour repérer une migration jamais appliquée, sans
    maintenir une liste qui se périmerait à chaque table ajoutée.
    """
    expected = {module.name for module in ENABLED_MODULES if module.models_module is not None}
    return {name for name in expected if not any(table.startswith(f"{name}_") for table in present)}


async def _check_storage(settings: Settings) -> list[Check]:
    """Le stockage répond-il, et les buckets existent-ils ?"""
    async with AsyncExitStack() as stack:
        try:
            storage = await S3ObjectStorage.open(
                settings.storage, settings.app.environment.value, stack
            )
            await storage.check_health()
        except Exception as exc:
            return [
                Check(
                    "Stockage objet",
                    passed=False,
                    detail=f"injoignable ({type(exc).__name__})",
                    fix="make db-up puis make storage-provision",
                )
            ]

        missing: list[str] = []
        for bucket in Bucket:
            name = storage.bucket_name(bucket)
            try:
                await storage.list(bucket, prefix="")
            except Exception:
                missing.append(name)

        return [
            Check(
                "Stockage objet",
                passed=True,
                detail=f"{settings.storage.provider.value} joignable",
            ),
            Check(
                "Buckets",
                passed=not missing,
                detail=f"{len(REGISTRY) - len(missing)}/{len(REGISTRY)} disponibles"
                + (f", manquants : {', '.join(missing)}" if missing else ""),
                fix="make storage-provision",
            ),
        ]


def _check_secrets(settings: Settings) -> list[Check]:
    """Les secrets sont-ils dignes de l'environnement visé ?"""
    deployed = settings.app.environment.is_deployed
    pepper = settings.security.token_pepper.get_secret_value()
    secret = settings.security.secret_key.get_secret_value()

    return [
        Check(
            "Clé secrète",
            passed=len(secret) >= MIN_SECRET_LENGTH,
            detail="définie" if len(secret) >= MIN_SECRET_LENGTH else "faible ou par défaut",
            fix='python -c "import secrets; print(secrets.token_urlsafe(64))"',
            fatal=deployed,
        ),
        Check(
            "Poivre des jetons",
            passed=bool(pepper),
            detail="défini" if pepper else "absent",
            fix="renseigner SECURITY__TOKEN_PEPPER",
            fatal=deployed,
        ),
    ]


async def _check_delivery(settings: Settings) -> list[Check]:
    """Les codes de connexion peuvent-ils réellement atteindre quelqu'un ?

    Le contrôle **joint le fournisseur** quand il y en a un : une
    configuration syntaxiquement correcte mais refusée par le serveur ne se
    découvre autrement qu'au premier utilisateur bloqué à l'inscription.
    """
    deployed = settings.app.environment.is_deployed

    if settings.mail.provider is MailProvider.CONSOLE:
        return [
            Check(
                "Envoi des e-mails",
                passed=not deployed,
                detail="console : les codes s'affichent dans les journaux",
                fix="MAIL__PROVIDER=smtp et les identifiants qui vont avec",
                fatal=deployed,
            )
        ]
    return [await _probe("Envoi des e-mails", build_mailer(settings.mail), "MAIL__")]


async def _probe(label: str, adapter: Any, prefix: str) -> Check:
    """Joint un fournisseur et rend le verdict.

    Le détail technique du fournisseur est repris ici — il ne sort jamais
    vers un client, mais c'est exactement ce dont on a besoin en console.
    """
    try:
        await adapter.check_health()
    except Exception as exc:
        detail = getattr(exc, "technical_detail", "") or type(exc).__name__
        return Check(
            label,
            passed=False,
            detail=f"le fournisseur refuse : {detail}",
            fix=f"vérifier les variables {prefix}*",
        )
    return Check(label, passed=True, detail="fournisseur joignable")


def _check_permissions() -> list[Check]:
    """L'inventaire des permissions est-il cohérent ?"""
    known = all_permissions()
    return [
        Check(
            "Permissions déclarées",
            passed=len(known) == len(set(known)),
            detail=f"{len(known)} permissions sur {len(ENABLED_MODULES)} modules",
            fix="deux modules déclarent la même permission",
        )
    ]


async def run(settings: Settings) -> int:
    """Exécute tous les contrôles et rend un code de sortie."""
    print(f"\nEnvironnement : {settings.app.environment.value}\n")

    checks: list[Check] = []
    checks += _check_secrets(settings)
    checks += await _check_database(settings)
    checks += await _check_storage(settings)
    checks += _check_permissions()
    checks += await _check_delivery(settings)

    for check in checks:
        print(check.render())

    failed = [c for c in checks if not c.passed and c.fatal]
    warned = [c for c in checks if not c.passed and not c.fatal]

    print()
    if failed:
        print(f"{len(failed)} contrôle(s) en échec. L'environnement n'est pas prêt.")
        return 1
    if warned:
        print(f"Tout fonctionne. {len(warned)} point(s) d'attention ci-dessus.")
        return 0
    print("Tout est opérationnel.")
    return 0


def main() -> int:
    """Point d'entrée."""
    settings = get_settings()
    if settings.app.environment is Environment.TEST:
        print("Le diagnostic ne s'exécute pas en environnement de test.")
        return 0
    return asyncio.run(run(settings))


if __name__ == "__main__":
    sys.exit(main())
