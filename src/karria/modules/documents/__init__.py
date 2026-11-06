"""Module `documents` — modèles de CV, exports PDF et DOCX, score ATS (ADR-0020).

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module (règle 3).

Ce que le module possède : le catalogue des modèles et leurs versions
(spécification tirée du PDF, mesures, aperçus), la bibliothèque de polices
libres, ce que chaque personne possède et a choisi, ses exports et leur score
ATS. Ce qu'il ne possède pas : le CV lui-même (lu par le port `CvSource`,
branché sur le module `cv` dans `bootstrap/wiring.py`), le compte, l'encaissement
d'un modèle Premium (`payments`, qui lit la vitrine `TemplateShop` et remet le
modèle payé par `grant`).

`build_forget_user` est la réaction à la suppression d'un compte ;
`register_tasks` inscrit ses travaux de file (ingestion d'un modèle,
explication d'un score, envoi par e-mail) ; `build_submit_template`,
`build_ingest_template`, `build_refresh_template_colors` (la couleur de galerie)
et `build_publish_plain_letter` (les aperçus de la lettre sobre, ADR-0036) servent la
commande `scripts/templates.py`.

La façade de l'atelier (ADR-0029), pour un autre module (règle 4, via
`bootstrap`) : `RenderDocument` / `RenderedDocument` (le cas d'usage et sa
réponse), `get_render_document` (dépendance FastAPI) et
`build_render_document(settings, storage, cvs=…)` (hors requête). Le CV
arrive **en données** (forme de `GET /cv/me`) ; `letter=` rend une lettre
(ADR-0030). `AccountDirectory` / `get_account_directory` est le port que
`bootstrap` branche sur `identity` pour l'envoi à l'adresse du compte
(ADR-0031).
"""

from karria.modules.documents.api import router
from karria.modules.documents.api.dependencies import (
    build_forget_user,
    build_ingest_template,
    build_publish_plain_letter,
    build_refresh_template_colors,
    build_render_document,
    build_submit_template,
    get_account_directory,
    get_cv_source,
    get_render_document,
    get_template_shop,
)
from karria.modules.documents.api.tasks import register_tasks
from karria.modules.documents.application import (
    AccountDirectory,
    CvSnapshot,
    CvSource,
    ForgetUser,
    PremiumTemplate,
    RenderDocument,
    RenderedDocument,
    TemplateShop,
)
from karria.modules.documents.infrastructure import ProcrastinateTemplateJobs
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="documents",
    router=router,
    models_module="karria.modules.documents.infrastructure.models",
)

__all__ = [
    "MODULE",
    "AccountDirectory",
    "CvSnapshot",
    "CvSource",
    "ForgetUser",
    "PremiumTemplate",
    "ProcrastinateTemplateJobs",
    "RenderDocument",
    "RenderedDocument",
    "TemplateShop",
    "build_forget_user",
    "build_ingest_template",
    "build_publish_plain_letter",
    "build_refresh_template_colors",
    "build_render_document",
    "build_submit_template",
    "get_account_directory",
    "get_cv_source",
    "get_render_document",
    "get_template_shop",
    "register_tasks",
]
