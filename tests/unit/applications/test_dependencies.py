"""Les ports non branchés refusent, et l'assemblage hors requête tient."""

from __future__ import annotations

from uuid import uuid4

import pytest

from doubles import PermissiveCvValidator
from karria.modules.applications.api.dependencies import (
    UnwiredBaseCvReader,
    UnwiredCvValidator,
    UnwiredOfferReader,
    UnwiredTemplateChecker,
    build_adapt_cv,
    build_forget_user,
    get_base_cv_reader,
    get_cv_validator,
    get_logo_url,
    get_offer_reader,
    get_template_checker,
)
from karria.modules.applications.application import AdaptCv, ForgetUser
from karria.shared.config import AppSettings, Environment, Settings
from karria.shared.errors import DependencyUnavailableError

pytestmark = pytest.mark.unit


async def test_les_ports_non_branches_refusent() -> None:
    assert isinstance(get_offer_reader(), UnwiredOfferReader)
    assert await get_offer_reader().read(uuid4()) is None
    assert isinstance(get_base_cv_reader(), UnwiredBaseCvReader)
    assert await get_base_cv_reader().read(uuid4()) is None
    assert isinstance(get_template_checker(), UnwiredTemplateChecker)
    assert await get_template_checker().usable(uuid4(), uuid4()) is False
    assert get_logo_url() is None
    assert isinstance(get_cv_validator(), UnwiredCvValidator)
    with pytest.raises(DependencyUnavailableError):
        get_cv_validator().validate({})


async def test_l_assemblage_hors_requete() -> None:
    settings = Settings(app=AppSettings(environment=Environment.TEST))

    assert isinstance(build_adapt_cv(settings, PermissiveCvValidator), AdaptCv)
    assert isinstance(build_forget_user(), ForgetUser)
    with pytest.raises(RuntimeError):
        await build_adapt_cv(settings, PermissiveCvValidator)._jobs.defer_adaptation(uuid4())
