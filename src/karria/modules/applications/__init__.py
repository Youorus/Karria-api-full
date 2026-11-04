"""Module `applications` — les dossiers de candidature : un CV adapté à une offre, une lettre, une trace.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module (règle 3).

Ce que le module possède : le dossier de candidature (ADR-0033) — l'offre
figée au moment de l'ouverture, la **copie** du CV que Karria adapte et que
la personne retouche, les propositions de Karria et ce qu'elle en a fait,
la lettre de motivation, la couverture des mots-clés, « j'ai candidaté ».
Ce qu'il ne possède pas : le CV de base (lu chez `cv` par le port
`BaseCvReader`, jugé par `CvValidator`), l'offre (lue chez `offers` par
`OfferReader`, logo par `LogoUrl`), le modèle de CV (vérifié chez
`documents` par `TemplateChecker`), le rendu et les exports (`documents`,
que l'app appelle avec le CV du dossier et son identifiant).

Ce qu'il expose, et pour qui :

- `MODULE` — la déclaration auprès du registre ;
- les ports vers les autres modules et leurs fournisseurs par défaut
  (`get_offer_reader`, `get_base_cv_reader`, `get_cv_validator`,
  `get_template_checker`, `get_logo_url`), que `bootstrap/wiring.py`
  branche (règle 4) ;
- `DossierApplied`, `DossierUnapplied` — les événements qu'un pont du bus
  peut relayer au fil d'offres ;
- `ForgetUser`, `build_forget_user` — la réaction à la suppression d'un
  compte, abonnée au bus par `bootstrap/wiring.py` (ADR-0015) ;
- `register_tasks` — le travail `applications.adapt_cv`, inscrit par
  `bootstrap/worker.py`.
"""

from karria.modules.applications.api import router
from karria.modules.applications.api.dependencies import (
    build_forget_user,
    get_base_cv_reader,
    get_cv_validator,
    get_logo_url,
    get_offer_reader,
    get_template_checker,
)
from karria.modules.applications.api.tasks import register_tasks
from karria.modules.applications.application import (
    BaseCv,
    BaseCvReader,
    CvValidator,
    DossierApplied,
    DossierUnapplied,
    ForgetUser,
    LogoUrl,
    OfferCard,
    OfferReader,
    TemplateChecker,
)
from karria.modules.applications.domain import CvInvalidError
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="applications",
    router=router,
    models_module="karria.modules.applications.infrastructure.models",
)

__all__ = [
    "MODULE",
    "BaseCv",
    "BaseCvReader",
    "CvInvalidError",
    "CvValidator",
    "DossierApplied",
    "DossierUnapplied",
    "ForgetUser",
    "LogoUrl",
    "OfferCard",
    "OfferReader",
    "TemplateChecker",
    "build_forget_user",
    "get_base_cv_reader",
    "get_cv_validator",
    "get_logo_url",
    "get_offer_reader",
    "get_template_checker",
    "register_tasks",
]
