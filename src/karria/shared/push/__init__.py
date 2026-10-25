"""Le contrat des notifications push partagé par tous les émetteurs (voir `contract.py`)."""

from karria.shared.push.contract import (
    MAX_BODY_LENGTH,
    MAX_TITLE_LENGTH,
    PUSH_CONTRACT_VERSION,
    PushChannel,
    PushKind,
    PushMessage,
)

__all__ = [
    "MAX_BODY_LENGTH",
    "MAX_TITLE_LENGTH",
    "PUSH_CONTRACT_VERSION",
    "PushChannel",
    "PushKind",
    "PushMessage",
]
