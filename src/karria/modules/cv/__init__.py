"""Module `cv` — le CV d'une personne, créé en conversation avec Gemini.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module. Tout ce qui n'y est
pas exporté est privé, quelle que soit sa visibilité Python (règle 3).

Ce que le module possède : le brouillon de CV (chapitre par chapitre), le
CV lui-même, les photos en attente de choix, et tout appel à Gemini qui les
concerne — interprétation, transcription, extraction, portrait. Ce qu'il ne
possède pas : le compte (référencé par `user_id`, sans clé étrangère), la
génération de documents (le module `documents` lit ce CV par `GetMyCv`,
branché dans `bootstrap/wiring.py`).

`ForgetUser` et `build_forget_user` sont la réaction à la suppression d'un
compte : `bootstrap/wiring.py` les abonne à l'événement du module des
comptes (ADR-0015). Aucun autre module ne les appelle.

`register_tasks`, `run_refresh`, `build_refresh_suggestions` et le port
`OfferVocabulary` servent les **suggestions à la saisie** (ADR-0028) :
`bootstrap/worker.py` inscrit le travail nocturne en lui passant le
vocabulaire de l'index d'offres ; `scripts/suggestions.py` le lance à la
demande. `SuggestionKind` nomme ce qu'on complète.
"""

from karria.modules.cv.api import router
from karria.modules.cv.api.dependencies import (
    build_forget_user,
    build_refresh_suggestions,
    get_get_my_cv,
)
from karria.modules.cv.api.tasks import register_tasks, run_refresh
from karria.modules.cv.application import (
    CvFinalized,
    ForgetOutcome,
    ForgetUser,
    GetMyCv,
    OfferVocabulary,
    SuggestionRefreshReport,
)
from karria.modules.cv.domain import (
    Chapter,
    ChapterStatus,
    Contact,
    CurriculumVitae,
    Education,
    Experience,
    Identity,
    Interest,
    InterestsChapter,
    Language,
    LanguageLevel,
    Project,
    Skill,
    SkillLevel,
    SkillsChapter,
    SuggestionKind,
    Summary,
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
    "GetMyCv",
    "Identity",
    "Interest",
    "InterestsChapter",
    "Language",
    "LanguageLevel",
    "OfferVocabulary",
    "Project",
    "Skill",
    "SkillLevel",
    "SkillsChapter",
    "SuggestionKind",
    "SuggestionRefreshReport",
    "Summary",
    "build_forget_user",
    "build_refresh_suggestions",
    "get_get_my_cv",
    "register_tasks",
    "run_refresh",
]
