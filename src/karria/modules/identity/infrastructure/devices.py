"""Ce qu'`identity` sait des appareils quand personne ne lui a rien branché : rien.

`MainDeviceLookup` est un port vers un autre module (`devices`). Tant que
`bootstrap/wiring.py` n'y a pas branché la vraie réponse, cette
implémentation répond « inconnu » : la trace de départ perd deux colonnes,
la suppression de compte n'échoue pas. C'est la règle de tout branchement
(voir `bootstrap/wiring.py`) : un oubli dégrade, il ne casse pas.
"""

from __future__ import annotations

from uuid import UUID

from karria.modules.identity.application import MainDeviceSnapshot


class UnknownMainDeviceLookup:
    """Implémente `MainDeviceLookup` sans rien savoir."""

    async def main_device_of(self, user_id: UUID) -> MainDeviceSnapshot | None:
        """Toujours `None` : aucun appareil n'est connu d'ici."""
        _ = user_id
        return None
