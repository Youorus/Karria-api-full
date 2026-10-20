"""Doublures partagées par la suite de tests.

Importables depuis n'importe quel test, et pas seulement injectées par les
fixtures : un test qui veut construire sa propre configuration de doublures
doit pouvoir le faire sans passer par `conftest.py`.

Une doublure n'hérite de rien : chaque port est un `Protocol`, satisfait
structurellement. C'est ce qui garantit qu'une doublure ne peut pas
dépendre d'un détail d'implémentation du vrai adaptateur.

Chaque module ajoute ici ses propres doublures — dépôt en mémoire,
fournisseur de modèle factice — dans le commit qui le crée.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

from karria.shared.events import DomainEvent, EventBus

# ---------------------------------------------------------------------
# Le temps
# ---------------------------------------------------------------------


class FakeClock:
    """Implémentation du port `Clock` dont on contrôle l'instant.

    Permet de vérifier qu'une règle horodate correctement, ou d'exercer une
    expiration sans attendre.
    """

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        """L'instant figé."""
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Fait avancer l'horloge."""
        self._now += delta


# ---------------------------------------------------------------------
# Le bus
# ---------------------------------------------------------------------


class CollectingBus(EventBus):
    """Bus réel qui conserve en plus tout ce qui a été publié.

    Hérite du vrai bus et non d'une doublure muette : les abonnés sont
    réellement appelés, et un test peut vérifier **à la fois** qu'un fait a
    été publié et que ses réactions ont eu lieu.
    """

    def __init__(self) -> None:
        super().__init__()
        self.published: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        """Publie et mémorise."""
        self.published.append(event)
        await super().publish(event)

    def of_type[EventT: DomainEvent](self, event_type: type[EventT]) -> list[EventT]:
        """Les événements publiés d'un type donné, dans l'ordre."""
        return [event for event in self.published if isinstance(event, event_type)]


# ---------------------------------------------------------------------
# Images : de vraies images, fabriquées à la volée
# ---------------------------------------------------------------------


def sample_image(
    width: int = 64,
    height: int = 48,
    *,
    fmt: str = "JPEG",
    alpha: bool = False,
    exif_orientation: int | None = None,
) -> bytes:
    """Une image réelle, décodable, aux dimensions demandées.

    Le socle image décode ce qu'il reçoit (ADR-0009) : des octets qui
    *commencent* comme un JPEG ne passeraient pas la porte. Une image de
    test doit en être une.

    Un dégradé plutôt qu'un aplat : une image uniforme se compresse en
    quelques centaines d'octets quelle que soit sa taille, et ne permet
    pas d'éprouver la réduction sous un plafond.
    """
    from PIL import Image  # noqa: PLC0415 - fabrique de test uniquement

    mode = "RGBA" if alpha else "RGB"
    image = Image.new(mode, (width, height))
    pixels = image.load()
    assert pixels is not None
    for x in range(width):
        for y in range(height):
            couleur = ((x * 7) % 256, (y * 13) % 256, ((x + y) * 3) % 256)
            pixels[x, y] = (*couleur, 128) if alpha else couleur
    buffer = BytesIO()
    options: dict[str, Any] = {}
    if fmt == "JPEG":
        options["quality"] = 95
        if exif_orientation is not None:
            exif = Image.Exif()
            exif[0x0112] = exif_orientation
            options["exif"] = exif.tobytes()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()
