"""API publique du stockage objet.

Capacité technique **transverse**, au même titre que la base de données ou
le bus d'événements : elle ne porte aucune règle métier, et plusieurs
modules doivent pouvoir l'utiliser. En faire un module métier serait une
faute — le contrat d'indépendance interdirait aux autres de l'importer.

Le domaine, lui, n'y a pas accès : un contrat `import-linter` le lui
interdit. Une règle métier qui atteindrait le stockage ne serait plus
testable en mémoire.
"""

from karria.shared.config.providers import PROFILES, Provider, ProviderProfile
from karria.shared.storage.buckets import (
    REGISTRY,
    Bucket,
    BucketPolicy,
    check_payload,
    physical_name,
)
from karria.shared.storage.errors import ObjectNotFoundError, PayloadRejectedError, StorageError
from karria.shared.storage.factory import build_object_storage
from karria.shared.storage.keys import (
    KEY_MAX_LENGTH,
    InvalidObjectKeyError,
    build_key,
    ensure_key_under,
    extension_for,
    is_valid_key,
)
from karria.shared.storage.memory import InMemoryObjectStorage
from karria.shared.storage.ports import (
    ObjectStorage,
    ObjectSummary,
    PresignedUpload,
    StoredObject,
)
from karria.shared.storage.s3 import S3ObjectStorage
from karria.shared.storage.uploads import PreparedUpload, prepare_upload

__all__ = [
    "KEY_MAX_LENGTH",
    "PROFILES",
    "REGISTRY",
    "Bucket",
    "BucketPolicy",
    "InMemoryObjectStorage",
    "InvalidObjectKeyError",
    "ObjectNotFoundError",
    "ObjectStorage",
    "ObjectSummary",
    "PayloadRejectedError",
    "PreparedUpload",
    "PresignedUpload",
    "Provider",
    "ProviderProfile",
    "S3ObjectStorage",
    "StorageError",
    "StoredObject",
    "build_key",
    "build_object_storage",
    "check_payload",
    "ensure_key_under",
    "extension_for",
    "is_valid_key",
    "physical_name",
    "prepare_upload",
]
