"""Module `documents` — modèles de CV, exports PDF et DOCX, score ATS (ADR-0020).

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module (règle 3).

Ce que le module possède : le catalogue des modèles et leurs versions
(spécification tirée du PDF, mesures, aperçus), la bibliothèque de polices
libres, ce que chaque personne possède et a choisi, ses exports et leur score
ATS. Ce qu'il ne possède pas : le CV lui-même (lu par le port `CvSource`,
branché sur le module `cv` dans `bootstrap/wiring.py`), le compte, l'achat
d'un modèle Pro.

`build_forget_user` est la réaction à la suppression d'un compte ;
`register_tasks` inscrit ses travaux de file (ingestion d'un modèle,
explication d'un score) ; `build_submit_template` et `build_ingest_template`
servent la commande `scripts/templates.py`.
"""

from karria.modules.documents.api import router
from karria.modules.documents.api.dependencies import (
    build_forget_user,
    build_ingest_template,
    build_submit_template,
    get_cv_source,
)
from karria.modules.documents.api.tasks import register_tasks
from karria.modules.documents.application import CvSnapshot, CvSource, ForgetUser
from karria.modules.documents.infrastructure import ProcrastinateTemplateJobs
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="documents",
    router=router,
    models_module="karria.modules.documents.infrastructure.models",
)

__all__ = [
    "MODULE",
    "CvSnapshot",
    "CvSource",
    "ForgetUser",
    "ProcrastinateTemplateJobs",
    "build_forget_user",
    "build_ingest_template",
    "build_submit_template",
    "get_cv_source",
    "register_tasks",
]
