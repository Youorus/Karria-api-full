"""Module `cv` — le CV d'une personne, créé en conversation avec Gemini.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module. Tout ce qui n'y est
pas exporté est privé, quelle que soit sa visibilité Python (règle 3).

Ce que le module possède : le brouillon de CV (chapitre par chapitre), le
CV lui-même, les photos en attente de choix, et tout appel à Gemini qui les
concerne — interprétation, transcription, extraction, portrait. Ce qu'il ne
possède pas : le compte (référencé par `user_id`, sans clé étrangère), la
génération de documents (qui lira ce CV par cette API publique).

`ForgetUser` et `build_forget_user` sont la réaction à la suppression d'un
compte : `bootstrap/wiring.py` les abonne à l'événement du module des
comptes (ADR-0015). Aucun autre module ne les appelle.
"""

from karria.modules.cv.api import router
from karria.modules.cv.api.dependencies import build_forget_user
from karria.modules.cv.application import CvFinalized, ForgetOutcome, ForgetUser
from karria.modules.cv.domain import (
    Chapter,
    ChapterStatus,
    Contact,
    CurriculumVitae,
    Education,
    Experience,
    Identity,
    Interest,
    Language,
    LanguageLevel,
    Project,
    Skill,
    SkillLevel,
)
from karria.shared.module import ModuleDescriptor

#: Déclaration du module auprès de l'application. Les tables sont
#: découvertes par `models_module` : impossible d'enregistrer le module
#: sans migrer ses tables.
MODULE = ModuleDescriptor(
    name="cv",
    router=router,
    models_module="karria.modules.cv.infrastructure.models",
)

__all__ = [
    "MODULE",
    "Chapter",
    "ChapterStatus",
    "Contact",
    "CurriculumVitae",
    "CvFinalized",
    "Education",
    "Experience",
    "ForgetOutcome",
    "ForgetUser",
    "Identity",
    "Interest",
    "Language",
    "LanguageLevel",
    "Project",
    "Skill",
    "SkillLevel",
    "build_forget_user",
]
