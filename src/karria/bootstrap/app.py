"""Fabrique de l'application FastAPI.

C'est le **seul** point d'assemblage : configuration, journalisation,
middlewares, gestionnaires d'erreurs et routes s'y rencontrent. Le reste du
code ignore que cet assemblage existe.

Passer par une fabrique plutôt que par un objet `app` de niveau module a une
conséquence pratique décisive : un test construit sa propre application, avec
sa propre configuration et ses propres surcharges de dépendances, sans
aucun état partagé entre tests.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from starlette.middleware.trustedhost import TrustedHostMiddleware

from karria.bootstrap.lifespan import lifespan
from karria.bootstrap.modules import ENABLED_MODULES
from karria.bootstrap.wiring import wire_modules
from karria.shared.config import Environment, Settings, get_settings
from karria.shared.errors import register_exception_handlers
from karria.shared.http import RequestContextMiddleware, SecurityHeadersMiddleware
from karria.shared.logging import configure_logging


def _operation_id(route: APIRoute) -> str:
    """Produit un `operationId` lisible et stable dans le schéma OpenAPI.

    Par défaut FastAPI génère des identifiants du type
    `read_item_api_v1_items__item_id__get`, qui deviennent des noms de
    méthodes illisibles chez tout client généré automatiquement. On les
    dérive du tag et du nom de la fonction, tous deux sous notre contrôle.
    """
    tag = str(route.tags[0]) if route.tags else "default"
    return f"{tag}_{route.name}"


def _configure_middlewares(app: FastAPI, settings: Settings) -> None:
    """Empile les middlewares.

    L'ordre compte : FastAPI enveloppe l'application avec le **dernier**
    middleware ajouté en position la plus externe. On ajoute donc du plus
    interne au plus externe, pour obtenir la pile suivante :

        RequestContext → TrustedHost → CORS → SecurityHeaders → routes

    `RequestContext` est le plus externe afin que l'identifiant de
    corrélation soit disponible pour absolument tout ce qui suit, y compris
    un rejet par `TrustedHost`.
    """
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.app.environment is Environment.PRODUCTION,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.security.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construit une application prête à servir.

    Args:
        settings: Configuration à utiliser. Par défaut celle du processus ;
            un test peut en injecter une autre sans toucher l'environnement.
    """
    settings = settings or get_settings()
    configure_logging(settings.observability)

    is_production = settings.app.environment is Environment.PRODUCTION

    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        lifespan=lifespan,
        # Pas de `default_response_class` : depuis FastAPI 0.141, la
        # sérialisation JSON passe directement par Pydantic (en Rust), ce
        # qui est plus rapide qu'un aller-retour par orjson. Imposer
        # `ORJSONResponse` — comme le faisait la V1 — est désormais un
        # ralentissement doublé d'un avertissement de dépréciation.
        generate_unique_id_function=_operation_id,
        # La documentation interactive décrit la surface complète de l'API :
        # elle reste ouverte partout sauf en production, où elle est fermée
        # par défaut. Le schéma reste généré et versionnable hors ligne.
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
    )

    _configure_middlewares(app, settings)
    register_exception_handlers(app)

    for module in ENABLED_MODULES:
        if module.router is None:
            continue
        prefix = settings.app.api_prefix if module.use_api_prefix else ""
        app.include_router(module.router, prefix=prefix)

    wire_modules(app)

    return app
