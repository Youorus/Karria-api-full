"""Une photo envoyée pour le CV, en attente d'être retenue.

`POST /cv/photo` en produit une (ou deux : l'originale, puis l'améliorée) ;
le chapitre `photo` du brouillon en retient une par son identifiant ; la
finalisation la recopie dans le CV et oublie les autres. C'est ce qui évite
de stocker une URL : le client ne nous rend qu'un identifiant, et la clé
objet ne sort jamais de chez nous (ADR-0008).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CvPhoto:
    """Référence d'une photo écrite dans le bucket `media`."""

    id: UUID
    user_id: UUID
    image_key: str
    enhanced: bool
    created_at: datetime
