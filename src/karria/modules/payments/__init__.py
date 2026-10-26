"""Module `payments` — Encaissements Stripe Checkout v2 pour cafés et modèles."""

from karria.modules.payments.api.routers import router
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="payments",
    router=router,
    models_module="",
)

__all__ = ["MODULE"]
