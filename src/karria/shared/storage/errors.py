"""Erreurs du stockage objet.

Règle de diagnostic, tirée d'un défaut mesuré sur la V1 `karria_api` : **conserver
le code d'erreur du fournisseur** dans les détails, et **jamais** la clé
d'accès ni l'URL présignée. La V1 remonte `RuntimeError("Erreur upload
S3.")` — sans code, sans bucket, sans clé : un incident de production y
devient une enquête à l'aveugle.

Une URL présignée contient une signature valide plusieurs heures : la
journaliser revient à publier un droit d'accès temporaire dans les
journaux, où il survivra bien plus longtemps que l'incident.
"""

from __future__ import annotations

from typing import ClassVar

from karria.shared.errors.exceptions import DomainError, ErrorCode, InfrastructureError


class StorageError(InfrastructureError):
    """Défaillance du stockage objet.

    Classée en `InfrastructureError` et non en erreur de domaine : c'est une
    panne, pas une faute de l'appelant. On réessaie une panne, on ne
    réessaie pas une règle métier.
    """

    code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_FAILURE

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        bucket: str,
        key: str | None = None,
        provider_code: str | None = None,
    ) -> None:
        details: dict[str, str] = {"operation": operation, "bucket": bucket}
        if key is not None:
            details["key"] = key
        if provider_code is not None:
            # Le code du fournisseur — `AccessDenied`, `NoSuchBucket`… — est
            # ce qui transforme une enquête en diagnostic.
            details["provider_code"] = provider_code
        super().__init__(message, details=details)


class ObjectNotFoundError(StorageError):
    """La clé demandée n'existe pas.

    Distinguée du reste : un fichier absent est un cas **métier** — le
    compte n'a pas de photo, le document a été purgé — tandis qu'un service
    injoignable est un incident. Les confondre sous un `except ClientError`
    en bloc fait passer une panne pour une absence, et réciproquement.
    """

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND


class PayloadRejectedError(DomainError):
    """Taille ou type MIME refusé par la politique du bucket.

    Erreur de **domaine** et non d'infrastructure : l'appelant a envoyé
    quelque chose d'inacceptable, le stockage va parfaitement bien. Elle est
    levée avant tout appel réseau.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_INPUT

    def __init__(self, message: str, *, field: str = "file") -> None:
        super().__init__(message, details={"reason": "storage_payload_rejected", "field": field})
