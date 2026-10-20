"""API publique du bus d'événements."""

from karria.shared.events.base import DomainEvent
from karria.shared.events.bus import EventBus, EventHandler

__all__ = ["DomainEvent", "EventBus", "EventHandler"]
