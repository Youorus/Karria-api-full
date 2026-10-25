"""Configuration de la journalisation structurée.

Contrat
-------
- **Un seul point de configuration** pour tout le processus, y compris les
  logs émis par uvicorn et par les bibliothèques tierces. Une application
  qui produit deux formats de logs n'est pas exploitable par un agrégateur.
- **Structuré par défaut** : on journalise des paires clé/valeur, pas des
  phrases. `logger.info("user.created", user_id=...)` est requêtable ;
  `logger.info(f"user {uid} created")` ne l'est pas.
- **JSON en production, lisible en local.** Le format est un réglage
  d'environnement, jamais une branche dans le code applicatif.
- **Les secrets sont masqués par un processeur**, pas par la discipline des
  développeurs. On ne peut pas fuiter ce que le pipeline efface.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Final

import orjson
import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from karria.shared.config import ObservabilitySettings

#: Fragments de clés dont la valeur ne doit jamais atteindre les logs.
_SENSITIVE_KEY_FRAGMENTS: Final = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "credential",
)

_REDACTED: Final = "***"

#: Loggers tiers réacheminés vers notre pipeline, pour un format unique.
_MANAGED_LOGGERS: Final = ("uvicorn", "uvicorn.error", "uvicorn.access", "tortoise", "aerich")


def _redact_sensitive_values(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Masque toute valeur dont la clé évoque un secret."""
    for key in event_dict:
        lowered = key.lower()
        if any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS):
            event_dict[key] = _REDACTED
    return event_dict


def _serialize(obj: Any, /, **_: Any) -> str:
    """Sérialise en JSON via orjson, avec repli `str` sur les types exotiques."""
    return orjson.dumps(obj, default=str).decode()


def configure_logging(settings: ObservabilitySettings) -> None:
    """Installe le pipeline de journalisation pour l'ensemble du processus."""
    shared_processors: list[Processor] = [
        # Réinjecte le correlation_id (et tout autre contexte lié) dans
        # chaque événement : c'est ce qui rend les logs corrélables.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        _redact_sensitive_values,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderers: list[Processor] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if settings.log_format == "json":
        renderers += [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(serializer=_serialize),
        ]
    else:
        renderers.append(structlog.dev.ConsoleRenderer(colors=True))

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # Les logs venant de bibliothèques tierces traversent la même
            # chaîne : un seul format en sortie, sans exception.
            foreign_pre_chain=shared_processors,
            processors=renderers,
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    for name in _MANAGED_LOGGERS:
        third_party = logging.getLogger(name)
        third_party.handlers = []
        third_party.propagate = True
    # httpx journalise chaque requête en INFO : des milliers par passage de
    # logos, qui noieraient le journal. Ses avertissements restent.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Retourne un logger structuré nommé.

    Convention : passer `__name__` pour que l'origine du log soit exploitable.
    """
    return structlog.stdlib.get_logger(name)
