"""La suppression d'un compte, de bout en bout, contre PostgreSQL et le schéma migré.

C'est ici que les ponts de `bootstrap/wiring.py` s'exercent pour de vrai :
`identity` efface et publie, `devices` et `cv` reçoivent et oublient, les
objets partent du stockage. Les dépôts sont les vrais ; seul le stockage est
en mémoire — le protocole S3 a ses propres tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doubles import CV_NOW, CollectingBus, FakeClock, complete_draft, sample_device_info
from karria.bootstrap.wiring import DevicesMainDeviceLookup, subscribe_bridges
from karria.modules.cv.domain import CurriculumVitae, CvPhoto, Draft
from karria.modules.cv.infrastructure import (
    TortoiseCvRepository,
    TortoiseDraftRepository,
    TortoisePhotoRepository,
)
from karria.modules.devices.application import GetMainDevice
from karria.modules.devices.domain import Device
from karria.modules.devices.infrastructure import TortoiseDeviceRepository
from karria.modules.identity.application import AccountDeleted, DeleteAccount
from karria.modules.identity.domain import (
    Consent,
    DepartureFeedback,
    DepartureReason,
    EmailAddress,
    ProviderIdentity,
    SocialProvider,
    User,
    open_challenge,
    open_session,
)
from karria.modules.identity.infrastructure import (
    TortoiseDepartureFeedbackRepository,
    TortoiseLoginChallengeRepository,
    TortoiseProviderIdentityRepository,
    TortoiseSessionRepository,
    TortoiseUserRepository,
)
from karria.modules.identity.infrastructure.models import DepartureFeedbackModel
from karria.shared.config import StorageSettings
from karria.shared.database import TortoiseUnitOfWork
from karria.shared.storage import Bucket, InMemoryObjectStorage

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_database")]

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
EMAIL = "partante@exemple.fr"


async def _seed_identity(user: User) -> None:
    await TortoiseUserRepository().add(user)
    await TortoiseSessionRepository().add(
        open_session(
            session_id=uuid4(),
            user_id=user.id,
            access_token_hash="a" * 64,
            refresh_token_hash="r" * 64,
            at=NOW,
            user_agent="karria/2.1.0",
        )
    )
    await TortoiseProviderIdentityRepository().add(
        ProviderIdentity(
            id=uuid4(),
            user_id=user.id,
            provider=SocialProvider.APPLE,
            subject="sub-apple",
            email=EMAIL,
            created_at=NOW,
            last_used_at=NOW,
        )
    )
    await TortoiseLoginChallengeRepository().add(
        open_challenge(
            challenge_id=uuid4(),
            email=EMAIL,
            user_id=user.id,
            code_hash="c" * 64,
            terms_version="2026-10",
            privacy_policy_version="2026-10",
            at=NOW,
        )
    )


async def _seed_devices(user: User) -> None:
    await TortoiseDeviceRepository().add(
        Device(
            id=uuid4(),
            user_id=user.id,
            installation_id="telephone",
            info=sample_device_info(),
            country="FR",
            first_seen_at=NOW,
            last_seen_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )


async def _seed_cv(user: User, storage: InMemoryObjectStorage) -> list[str]:
    keys = [f"users/{user.id}/cv-photo/retenue.jpg", f"users/{user.id}/cv-photo/attente.jpg"]
    for key in keys:
        await storage.put(Bucket.MEDIA, key, b"\xff\xd8octets", content_type="image/jpeg")
    await TortoiseDraftRepository().save(Draft.new(user.id, now=CV_NOW))
    await TortoiseCvRepository().save(
        CurriculumVitae.from_draft(
            complete_draft(user.id), cv_id=uuid4(), image_key=keys[0], created_at=CV_NOW, now=CV_NOW
        )
    )
    await TortoisePhotoRepository().add(
        CvPhoto(id=uuid4(), user_id=user.id, image_key=keys[1], enhanced=False, created_at=NOW)
    )
    return keys


class TestSuppressionDeBoutEnBout:
    async def test_tout_part_et_la_trace_est_anonyme(self) -> None:
        user = User(
            id=uuid4(),
            email=EmailAddress(EMAIL),
            email_verified_at=NOW,
            consent=Consent("2026-10", "2026-10", NOW),
            created_at=NOW,
            updated_at=NOW,
        )
        storage = InMemoryObjectStorage(StorageSettings())
        await _seed_identity(user)
        await _seed_devices(user)
        keys = await _seed_cv(user, storage)
        bus = CollectingBus()
        subscribe_bridges(bus, storage=storage)
        # `created_at` est posé par la base à l'insertion : l'âge se mesure
        # depuis l'instant réel, pas depuis la date figée des fixtures.
        clock = FakeClock(datetime.now(UTC) + timedelta(days=40))
        delete = DeleteAccount(
            users=TortoiseUserRepository(),
            identities=TortoiseProviderIdentityRepository(),
            sessions=TortoiseSessionRepository(),
            challenges=TortoiseLoginChallengeRepository(),
            departures=TortoiseDepartureFeedbackRepository(),
            main_device=DevicesMainDeviceLookup(GetMainDevice(devices=TortoiseDeviceRepository())),
            clock=clock,
            uow=TortoiseUnitOfWork(),
            events=bus,
        )

        await delete.execute(
            user_id=user.id,
            feedback=DepartureFeedback(reason=DepartureReason.NOT_USEFUL, details="Trop d'e-mails"),
        )

        # identity : plus rien.
        assert await TortoiseUserRepository().find_by_id(user.id) is None
        assert await TortoiseSessionRepository().find_by_access_fingerprint("a" * 64) is None
        assert (
            await TortoiseProviderIdentityRepository().find(SocialProvider.APPLE, "sub-apple")
            is None
        )
        assert await TortoiseLoginChallengeRepository().delete_for_email(EmailAddress(EMAIL)) == 0

        # La trace : la raison, l'âge, la plateforme — et rien qui désigne la personne.
        [row] = await DepartureFeedbackModel.all()
        assert row.reason == DepartureReason.NOT_USEFUL
        assert row.details == "Trop d'e-mails"
        assert row.account_age_days == 40
        assert (row.platform, row.app_version) == ("ios", "2.0.0")
        colonnes = set(DepartureFeedbackModel._meta.fields_map)
        assert not colonnes & {"user_id", "email", "user", "installation_id"}

        # Les ponts ont joué : devices et cv ont oublié la personne.
        assert bus.of_type(AccountDeleted)[0].user_id == user.id
        assert await TortoiseDeviceRepository().main_device_of(user.id) is None
        assert await TortoiseDraftRepository().find_by_user(user.id) is None
        assert await TortoiseCvRepository().find_by_user(user.id) is None
        assert await TortoisePhotoRepository().list_by_user(user.id) == []
        for key in keys:
            assert not await storage.exists(Bucket.MEDIA, key)


class TestDepotsDeSuppression:
    async def test_effacer_un_compte_absent_rend_false(self) -> None:
        assert await TortoiseUserRepository().delete(uuid4()) is False

    async def test_les_compteurs_disent_ce_qui_est_parti(self) -> None:
        user = User(
            id=uuid4(),
            email=EmailAddress(EMAIL),
            email_verified_at=NOW,
            consent=Consent("2026-10", "2026-10", NOW),
            created_at=NOW,
            updated_at=NOW,
        )
        await _seed_identity(user)
        await _seed_devices(user)

        assert await TortoiseSessionRepository().delete_all_of_user(user.id) == 1
        assert await TortoiseProviderIdentityRepository().delete_all_of_user(user.id) == 1
        assert await TortoiseLoginChallengeRepository().delete_for_email(EmailAddress(EMAIL)) == 1
        assert await TortoiseDeviceRepository().delete_all_of_user(user.id) == 1
        assert await TortoiseUserRepository().delete(user.id) is True
        await TortoiseCvRepository().delete_by_user(user.id)  # idempotent, sans ligne
