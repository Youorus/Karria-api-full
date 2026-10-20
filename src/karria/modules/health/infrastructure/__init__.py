"""Infrastructure du module `health`."""

from karria.modules.health.infrastructure.probes import DatabaseProbe, ObjectStorageProbe

__all__ = ["DatabaseProbe", "ObjectStorageProbe"]
