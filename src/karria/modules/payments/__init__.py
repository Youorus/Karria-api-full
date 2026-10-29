"""Module `payments` — encaissements Stripe Checkout (cafés de soutien, modèles de CV)."""

from karria.modules.payments.api.routers import router
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(name="payments", router=router)

__all__ = ["MODULE"]
