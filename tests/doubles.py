"""Doublures partagées par la suite de tests.

Importables depuis n'importe quel test, et pas seulement injectées par les
fixtures : un test qui veut construire sa propre configuration de doublures
doit pouvoir le faire sans passer par `conftest.py`.

Une doublure n'hérite de rien : chaque port est un `Protocol`, satisfait
structurellement. C'est ce qui garantit qu'une doublure ne peut pas
dépendre d'un détail d'implémentation du vrai adaptateur.

Chaque module ajoute ici ses propres doublures — dépôt en mémoire,
fournisseur de modèle factice — dans le commit qui le crée.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

from karria.modules.cv.application import GeneratedImage, Interpretation
from karria.modules.cv.domain import (
    AudioInfo,
    Chapter,
    ChapterSubmission,
    CurriculumVitae,
    CvPhoto,
    DocumentInfo,
    Draft,
)
from karria.modules.devices.application import StoredTicket
from karria.modules.devices.domain import (
    Device,
    DeviceInfo,
    Platform,
    PushEnvelope,
    PushError,
    PushPermission,
    PushReceipt,
    PushTicket,
)
from karria.modules.identity.application import ConsentGiven, MainDeviceSnapshot
from karria.modules.identity.domain import (
    AiConsent,
    DepartureRecord,
    EmailAddress,
    InvalidSocialTokenError,
    LoginChallenge,
    ProviderIdentity,
    Session,
    SessionEndReason,
    SessionNotFoundError,
    SocialProvider,
    TooManyAttemptsError,
    User,
    VerifiedSocialProfile,
)
from karria.modules.identity.infrastructure import Sha256TokenFactory
from karria.modules.support.application import (
    ConversationListing as SupportConversationListing,
)
from karria.modules.support.domain import Conversation as SupportConversation
from karria.modules.support.domain import Message as SupportMessage
from karria.shared.database import NullUnitOfWork
from karria.shared.events import DomainEvent, EventBus
from karria.shared.i18n import Language
from karria.shared.llm import Blob, LlmRequest, LlmResult, Provider
from karria.shared.push import PushMessage
from karria.shared.realtime import InMemoryBroker

# ---------------------------------------------------------------------
# Le temps
# ---------------------------------------------------------------------


class FakeClock:
    """Implémentation du port `Clock` dont on contrôle l'instant.

    Permet de vérifier qu'une règle horodate correctement, ou d'exercer une
    expiration sans attendre.
    """

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        """L'instant figé."""
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Fait avancer l'horloge."""
        self._now += delta


# ---------------------------------------------------------------------
# Le bus
# ---------------------------------------------------------------------


class CollectingBus(EventBus):
    """Bus réel qui conserve en plus tout ce qui a été publié.

    Hérite du vrai bus et non d'une doublure muette : les abonnés sont
    réellement appelés, et un test peut vérifier **à la fois** qu'un fait a
    été publié et que ses réactions ont eu lieu.
    """

    def __init__(self) -> None:
        super().__init__()
        self.published: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        """Publie et mémorise."""
        self.published.append(event)
        await super().publish(event)

    def of_type[EventT: DomainEvent](self, event_type: type[EventT]) -> list[EventT]:
        """Les événements publiés d'un type donné, dans l'ordre."""
        return [event for event in self.published if isinstance(event, event_type)]


# ---------------------------------------------------------------------
# Images : de vraies images, fabriquées à la volée
# ---------------------------------------------------------------------


def sample_image(
    width: int = 64,
    height: int = 48,
    *,
    fmt: str = "JPEG",
    alpha: bool = False,
    exif_orientation: int | None = None,
) -> bytes:
    """Une image réelle, décodable, aux dimensions demandées.

    Le socle image décode ce qu'il reçoit (ADR-0009) : des octets qui
    *commencent* comme un JPEG ne passeraient pas la porte. Une image de
    test doit en être une.

    Un dégradé plutôt qu'un aplat : une image uniforme se compresse en
    quelques centaines d'octets quelle que soit sa taille, et ne permet
    pas d'éprouver la réduction sous un plafond.
    """
    from PIL import Image  # noqa: PLC0415 - fabrique de test uniquement

    mode = "RGBA" if alpha else "RGB"
    image = Image.new(mode, (width, height))
    pixels = image.load()
    assert pixels is not None
    for x in range(width):
        for y in range(height):
            couleur = ((x * 7) % 256, (y * 13) % 256, ((x + y) * 3) % 256)
            pixels[x, y] = (*couleur, 128) if alpha else couleur
    buffer = BytesIO()
    options: dict[str, Any] = {}
    if fmt == "JPEG":
        options["quality"] = 95
        if exif_orientation is not None:
            exif = Image.Exif()
            exif[0x0112] = exif_orientation
            options["exif"] = exif.tobytes()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


# ---------------------------------------------------------------------
# Module `identity`
# ---------------------------------------------------------------------

#: Le consentement que l'app envoie « en continuant ».
SAMPLE_CONSENT = ConsentGiven(terms_version="2026-10", privacy_policy_version="2026-10")


class InMemoryUserRepository:
    """Implémentation en mémoire du port `UserRepository`."""

    def __init__(self) -> None:
        self.rows: dict[UUID, User] = {}

    async def find_by_id(self, user_id: UUID) -> User | None:
        return self.rows.get(user_id)

    async def find_by_email(self, email: EmailAddress) -> User | None:
        return next((u for u in self.rows.values() if u.email == email), None)

    async def add(self, user: User) -> None:
        self.rows[user.id] = user

    async def save(self, user: User) -> None:
        self.rows[user.id] = user

    async def delete(self, user_id: UUID) -> bool:
        return self.rows.pop(user_id, None) is not None


class InMemoryProviderIdentityRepository:
    """Implémentation en mémoire du port `ProviderIdentityRepository`."""

    def __init__(self) -> None:
        self.rows: dict[UUID, ProviderIdentity] = {}

    async def find(self, provider: SocialProvider, subject: str) -> ProviderIdentity | None:
        return next(
            (i for i in self.rows.values() if i.provider is provider and i.subject == subject),
            None,
        )

    async def add(self, identity: ProviderIdentity) -> None:
        self.rows[identity.id] = identity

    async def touch(self, identity_id: UUID, at: datetime) -> None:
        self.rows[identity_id] = replace(self.rows[identity_id], last_used_at=at)

    async def list_for(self, user_id: UUID) -> list[ProviderIdentity]:
        return [i for i in self.rows.values() if i.user_id == user_id]

    async def delete_all_of_user(self, user_id: UUID) -> int:
        doomed = [i.id for i in self.rows.values() if i.user_id == user_id]
        for iid in doomed:
            del self.rows[iid]
        return len(doomed)


class InMemorySessionRepository:
    """Implémentation en mémoire du port `SessionRepository`."""

    def __init__(self) -> None:
        self.rows: dict[UUID, Session] = {}

    async def add(self, session: Session) -> None:
        self.rows[session.id] = session

    async def save(self, session: Session) -> None:
        if session.id not in self.rows:
            raise SessionNotFoundError
        self.rows[session.id] = session

    async def find_by_access_fingerprint(self, fingerprint: str) -> Session | None:
        return next((s for s in self.rows.values() if s.access_token_hash == fingerprint), None)

    async def find_by_refresh_fingerprint(self, fingerprint: str) -> Session | None:
        return next(
            (s for s in self.rows.values() if s.refresh_token_hash == fingerprint and s.is_active),
            None,
        )

    async def find_by_previous_refresh(self, fingerprint: str) -> Session | None:
        return next(
            (s for s in self.rows.values() if s.previous_refresh_token_hash == fingerprint), None
        )

    async def active_of_user(self, user_id: UUID) -> list[Session]:
        return sorted(
            (s for s in self.rows.values() if s.user_id == user_id and s.is_active),
            key=lambda s: s.created_at,
            reverse=True,
        )

    async def end_all_of_user(
        self, user_id: UUID, *, reason: SessionEndReason, at: datetime
    ) -> int:
        closed = 0
        for session in list(self.rows.values()):
            if session.user_id == user_id and session.is_active:
                self.rows[session.id] = session.ended(reason=reason, at=at)
                closed += 1
        return closed

    async def delete_all_of_user(self, user_id: UUID) -> int:
        doomed = [s.id for s in self.rows.values() if s.user_id == user_id]
        for sid in doomed:
            del self.rows[sid]
        return len(doomed)

    async def purge_ended_before(self, moment: datetime) -> int:
        doomed = [s.id for s in self.rows.values() if s.refresh_expires_at < moment]
        for sid in doomed:
            del self.rows[sid]
        return len(doomed)


class InMemoryLoginChallengeRepository:
    """Implémentation en mémoire du port `LoginChallengeRepository`."""

    def __init__(self) -> None:
        self.rows: dict[UUID, LoginChallenge] = {}

    async def add(self, challenge: LoginChallenge) -> None:
        self.rows[challenge.id] = challenge

    async def save(self, challenge: LoginChallenge) -> None:
        self.rows[challenge.id] = challenge

    async def find(self, challenge_id: UUID) -> LoginChallenge | None:
        return self.rows.get(challenge_id)

    async def purge_before(self, moment: datetime) -> int:
        doomed = [c.id for c in self.rows.values() if c.expires_at < moment]
        for cid in doomed:
            del self.rows[cid]
        return len(doomed)

    async def delete_for_email(self, email: EmailAddress) -> int:
        doomed = [c.id for c in self.rows.values() if c.email == email.value]
        for cid in doomed:
            del self.rows[cid]
        return len(doomed)


class InMemoryDepartureFeedbackRepository:
    """Implémentation en mémoire du port `DepartureFeedbackRepository`."""

    def __init__(self) -> None:
        self.rows: list[DepartureRecord] = []

    async def add(self, record: DepartureRecord) -> None:
        self.rows.append(record)


class StaticMainDeviceLookup:
    """Port `MainDeviceLookup` : rend un appareil préparé, ou rien."""

    def __init__(self, snapshot: MainDeviceSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.calls: list[UUID] = []

    async def main_device_of(self, user_id: UUID) -> MainDeviceSnapshot | None:
        self.calls.append(user_id)
        return self.snapshot


class RecordingCodeSender:
    """Port `LoginCodeSender` : garde les codes au lieu de les envoyer.

    C'est ce qui permet à un test de « lire son e-mail ».
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Language]] = []
        self.fail_with: Exception | None = None

    async def send(
        self, *, email: str, code: str, expires_in_minutes: int, language: Language
    ) -> None:
        _ = expires_in_minutes
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((email, code, language))

    def last_code_for(self, email: str) -> str:
        return next(code for to, code, _ in reversed(self.sent) if to == email)


class PermissiveThrottle:
    """Port `LoginThrottle` : compte sans jamais refuser, sauf si on le lui dit."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.resets: list[str] = []
        self.blocked: set[str] = set()

    async def ensure_allowed(self, *, key: str) -> None:
        if key in self.blocked:
            raise TooManyAttemptsError(42)

    async def record_failure(self, *, key: str) -> None:
        self.failures.append(key)

    async def reset(self, *, key: str) -> None:
        self.resets.append(key)


class StubSocialVerifier:
    """Port `SocialTokenVerifier` : rend un profil préparé, ou lève."""

    def __init__(self, provider: SocialProvider, profile: VerifiedSocialProfile | None) -> None:
        self._provider = provider
        self.profile = profile
        self.calls: list[tuple[str, str | None]] = []

    @property
    def provider(self) -> SocialProvider:
        return self._provider

    async def verify(self, token: str, *, nonce: str | None) -> VerifiedSocialProfile:
        self.calls.append((token, nonce))
        if self.profile is None:
            raise InvalidSocialTokenError
        return self.profile


class StaticJwksSource:
    """Port `JwksSource` : sert un document fixé, et compte les relectures."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.fetches = 0
        self.refreshes = 0

    async def fetch(self, url: str) -> dict[str, Any]:
        _ = url
        self.fetches += 1
        return self.document

    async def refresh(self, url: str) -> dict[str, Any]:
        _ = url
        self.refreshes += 1
        return self.document


class InMemoryAiConsentRepository:
    """Implémentation en mémoire du port `AiConsentRepository`."""

    def __init__(self) -> None:
        self.rows: dict[UUID, AiConsent] = {}

    async def find(self, user_id: UUID) -> AiConsent | None:
        return self.rows.get(user_id)

    async def save(self, consent: AiConsent) -> None:
        self.rows[consent.user_id] = consent


class StaticAiProviderCatalog:
    """Implémentation du port `AiProviderCatalog` : les mêmes fournisseurs pour toute tâche."""

    def __init__(self, *providers: str) -> None:
        self.providers = frozenset(providers)
        self.asked: list[tuple[str, ...]] = []

    def providers_for(self, tasks: Sequence[str]) -> frozenset[str]:
        self.asked.append(tuple(tasks))
        return self.providers


@dataclass(slots=True)
class IdentityEnv:
    """Toutes les doublures du module `identity`, câblées ensemble."""

    users: InMemoryUserRepository
    identities: InMemoryProviderIdentityRepository
    sessions: InMemorySessionRepository
    challenges: InMemoryLoginChallengeRepository
    departures: InMemoryDepartureFeedbackRepository
    ai_consents: InMemoryAiConsentRepository
    ai_catalog: StaticAiProviderCatalog
    main_device: StaticMainDeviceLookup
    sender: RecordingCodeSender
    throttle: PermissiveThrottle
    tokens: Sha256TokenFactory
    clock: FakeClock
    uow: NullUnitOfWork
    events: CollectingBus

    @classmethod
    def build(cls) -> IdentityEnv:
        return cls(
            users=InMemoryUserRepository(),
            identities=InMemoryProviderIdentityRepository(),
            sessions=InMemorySessionRepository(),
            challenges=InMemoryLoginChallengeRepository(),
            departures=InMemoryDepartureFeedbackRepository(),
            ai_consents=InMemoryAiConsentRepository(),
            ai_catalog=StaticAiProviderCatalog("gemini"),
            main_device=StaticMainDeviceLookup(),
            sender=RecordingCodeSender(),
            throttle=PermissiveThrottle(),
            tokens=Sha256TokenFactory("poivre-de-test"),
            clock=FakeClock(),
            uow=NullUnitOfWork(),
            events=CollectingBus(),
        )


# ---------------------------------------------------------------------
# Module `devices`
# ---------------------------------------------------------------------


class InMemoryDeviceRepository:
    """Implémentation en mémoire du port `DeviceRepository`."""

    def __init__(self) -> None:
        self.rows: dict[UUID, Device] = {}

    async def find(self, user_id: UUID, installation_id: str) -> Device | None:
        return next(
            (
                d
                for d in self.rows.values()
                if d.user_id == user_id and d.installation_id == installation_id
            ),
            None,
        )

    async def add(self, device: Device) -> None:
        self.rows[device.id] = device

    async def save(self, device: Device) -> None:
        self.rows[device.id] = device

    async def delete(self, user_id: UUID, installation_id: str) -> bool:
        found = await self.find(user_id, installation_id)
        if found is None:
            return False
        del self.rows[found.id]
        return True

    async def push_targets_of_user(self, user_id: UUID) -> list[Device]:
        return [d for d in self.rows.values() if d.user_id == user_id and d.push_enabled]

    async def revoke_token(self, token: str, *, at: datetime) -> int:
        touched = 0
        for device in list(self.rows.values()):
            if device.info.push_token == token:
                self.rows[device.id] = device.token_revoked(at=at)
                touched += 1
        return touched

    async def main_device_of(self, user_id: UUID) -> Device | None:
        mine = [d for d in self.rows.values() if d.user_id == user_id]
        return max(mine, key=lambda d: d.last_seen_at, default=None)

    async def platform_stats(self) -> dict[Platform, int]:
        stats = dict.fromkeys(Platform, 0)
        for device in self.rows.values():
            stats[device.info.platform] += 1
        return stats

    async def delete_all_of_user(self, user_id: UUID) -> int:
        doomed = [d.id for d in self.rows.values() if d.user_id == user_id]
        for did in doomed:
            del self.rows[did]
        return len(doomed)


class InMemoryPushTicketRepository:
    """Implémentation en mémoire du port `PushTicketRepository`."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[StoredTicket, datetime, datetime | None]] = {}

    async def add_many(self, tickets: list[StoredTicket], *, at: datetime) -> None:
        for ticket in tickets:
            self.rows.setdefault(ticket.ticket_id, (ticket, at, None))

    async def pending(self, *, before: datetime, limit: int) -> list[StoredTicket]:
        eligible = sorted(
            (
                (created, ticket)
                for ticket, created, checked in self.rows.values()
                if checked is None and created < before
            ),
            key=lambda pair: pair[0],
        )
        return [ticket for _, ticket in eligible[:limit]]

    async def mark_checked(self, ticket_ids: list[str], *, at: datetime) -> None:
        for ticket_id in ticket_ids:
            ticket, created, _ = self.rows[ticket_id]
            self.rows[ticket_id] = (ticket, created, at)

    async def purge_checked_before(self, moment: datetime) -> int:
        doomed = [tid for tid, (_, _, checked) in self.rows.items() if checked and checked < moment]
        for tid in doomed:
            del self.rows[tid]
        return len(doomed)


class FakePushGateway:
    """Port `PushGateway` : rend des tickets programmés, enregistre les lots."""

    def __init__(self) -> None:
        self.batches: list[list[PushEnvelope]] = []
        self.receipt_queries: list[list[str]] = []
        #: Erreur à rendre pour un jeton donné ; les autres sont acceptés.
        self.errors: dict[str, PushError] = {}
        #: Reçus à rendre, par identifiant de ticket.
        self.receipts_by_ticket: dict[str, PushReceipt] = {}
        self._counter = 0

    async def send(self, envelopes: list[PushEnvelope]) -> list[PushTicket]:
        self.batches.append(list(envelopes))
        tickets: list[PushTicket] = []
        for envelope in envelopes:
            error = self.errors.get(envelope.token)
            if error is not None:
                tickets.append(PushTicket(token=envelope.token, ok=False, error=error))
                continue
            self._counter += 1
            tickets.append(
                PushTicket(token=envelope.token, ok=True, ticket_id=f"ticket-{self._counter}")
            )
        return tickets

    async def receipts(self, ticket_ids: list[str]) -> list[PushReceipt]:
        self.receipt_queries.append(list(ticket_ids))
        return [self.receipts_by_ticket[t] for t in ticket_ids if t in self.receipts_by_ticket]


def sample_device_info(
    *,
    platform: Platform = Platform.IOS,
    push_token: str | None = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]",  # noqa: S107
    permission: PushPermission = PushPermission.GRANTED,
) -> DeviceInfo:
    """Une déclaration d'appareil plausible."""
    return DeviceInfo(
        platform=platform,
        push_token=push_token,
        push_permission=permission,
        os_name="iOS",
        os_version="18.1",
        is_physical_device=True,
        app_version="2.0.0",
        app_build="42",
        device_brand="Apple",
        device_model="iPhone 15",
        locale="fr-FR",
        timezone="Europe/Paris",
        region="FR",
    )


@dataclass(slots=True)
class DevicesEnv:
    """Toutes les doublures du module `devices`, câblées ensemble."""

    devices: InMemoryDeviceRepository
    tickets: InMemoryPushTicketRepository
    gateway: FakePushGateway
    clock: FakeClock

    @classmethod
    def build(cls) -> DevicesEnv:
        return cls(
            devices=InMemoryDeviceRepository(),
            tickets=InMemoryPushTicketRepository(),
            gateway=FakePushGateway(),
            clock=FakeClock(),
        )


# ---------------------------------------------------------------------
# Module cv : dépôts en mémoire, Gemini factice, inspecteur factice
# ---------------------------------------------------------------------


class InMemoryDraftRepository:
    """Implémente `DraftRepository` dans un dictionnaire, par propriétaire."""

    def __init__(self) -> None:
        self.drafts: dict[UUID, Draft] = {}

    async def find_by_user(self, user_id: UUID) -> Draft | None:
        return self.drafts.get(user_id)

    async def save(self, draft: Draft) -> None:
        self.drafts[draft.user_id] = draft

    async def delete(self, draft_id: UUID) -> None:
        self.drafts = {user: d for user, d in self.drafts.items() if d.id != draft_id}


class InMemoryCvRepository:
    """Implémente `CvRepository` dans un dictionnaire, par propriétaire."""

    def __init__(self) -> None:
        self.cvs: dict[UUID, CurriculumVitae] = {}

    async def find_by_user(self, user_id: UUID) -> CurriculumVitae | None:
        return self.cvs.get(user_id)

    async def save(self, cv: CurriculumVitae) -> None:
        self.cvs[cv.user_id] = cv

    async def delete_by_user(self, user_id: UUID) -> None:
        self.cvs.pop(user_id, None)


class InMemoryPhotoRepository:
    """Implémente `PhotoRepository` dans un dictionnaire."""

    def __init__(self) -> None:
        self.photos: dict[UUID, CvPhoto] = {}

    async def add(self, photo: CvPhoto) -> None:
        self.photos[photo.id] = photo

    async def find(self, photo_id: UUID, *, user_id: UUID) -> CvPhoto | None:
        photo = self.photos.get(photo_id)
        return photo if photo is not None and photo.user_id == user_id else None

    async def list_by_user(self, user_id: UUID) -> list[CvPhoto]:
        return [p for p in self.photos.values() if p.user_id == user_id]

    async def delete(self, photo_ids: Sequence[UUID]) -> None:
        for photo_id in photo_ids:
            self.photos.pop(photo_id, None)


class FakeInterpreter:
    """Implémente `ChapterInterpreter` avec des réponses préparées, dans l'ordre."""

    def __init__(self, *results: Interpretation | Exception) -> None:
        self._results = list(results)
        self.calls: list[tuple[Chapter, str, dict[str, Any] | None]] = []

    async def interpret(
        self, chapter: Chapter, answer: str, correction_of: dict[str, Any] | None
    ) -> Interpretation:
        self.calls.append((chapter, answer, correction_of))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeTranscriber:
    """Implémente `AudioTranscriber` : rend un texte fixe, ou lève."""

    def __init__(self, text: str = "Bonjour.", error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls: list[tuple[int, str]] = []

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        self.calls.append((len(audio), content_type))
        if self._error is not None:
            raise self._error
        return self._text


class FakeEnhancer:
    """Implémente `PortraitEnhancer` : rend une image fixe, ou lève."""

    def __init__(
        self, result: GeneratedImage | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[int, str]] = []

    async def enhance(self, image: bytes, content_type: str) -> GeneratedImage:
        self.calls.append((len(image), content_type))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class FakeExtractor:
    """Implémente `CvDocumentExtractor` : rend une extraction fixe, ou lève."""

    def __init__(
        self, extracted: dict[Chapter, Any] | None = None, error: Exception | None = None
    ) -> None:
        self._extracted = extracted or {}
        self._error = error
        self.calls: list[tuple[bytes, str]] = []

    async def extract(self, document: bytes, content_type: str) -> dict[Chapter, Any]:
        self.calls.append((document, content_type))
        if self._error is not None:
            raise self._error
        return dict(self._extracted)


class FakeInspector:
    """Implémente `UploadInspector` sans lire les octets."""

    def __init__(
        self,
        *,
        pages: int | None = 1,
        duration: float | None = None,
        error: Exception | None = None,
        as_text: bool = False,
    ) -> None:
        self._pages = pages
        self._duration = duration
        self._error = error
        self._as_text = as_text

    def document(self, payload: bytes, content_type: str) -> DocumentInfo:
        if self._error is not None:
            raise self._error
        if self._as_text:
            return DocumentInfo(payload=payload, content_type="text/plain", pages=self._pages)
        return DocumentInfo(payload=payload, content_type=content_type, pages=self._pages)

    def audio(self, payload: bytes, content_type: str) -> AudioInfo:
        return AudioInfo(content_type=content_type, duration_seconds=self._duration)


class FakeLanguageModel:
    """Implémente `LanguageModel` : rend des résultats préparés, dans l'ordre, et garde les requêtes."""

    def __init__(self, *results: LlmResult | Exception) -> None:
        self._results = list(results)
        self.requests: list[LlmRequest] = []

    async def generate(self, request: LlmRequest) -> LlmResult:
        self.requests.append(request)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def text_result(text: str, *, finish_reason: str = "STOP") -> LlmResult:
    """Un résultat textuel, prêt pour une doublure."""
    return LlmResult(
        text=text,
        image=None,
        finish_reason=finish_reason,
        refused=False,
        provider=Provider.GEMINI,
        model="fake",
    )


def refused_result(reason: str = "SAFETY") -> LlmResult:
    """Un résultat refusé par le fournisseur."""
    return LlmResult(
        text=None,
        image=None,
        finish_reason=reason,
        refused=True,
        provider=Provider.GEMINI,
        model="fake",
    )


def image_result(payload: bytes, content_type: str = "image/jpeg") -> LlmResult:
    """Un résultat portant une image."""
    return LlmResult(
        text=None,
        image=Blob(payload, content_type),
        finish_reason="STOP",
        refused=False,
        provider=Provider.GEMINI,
        model="fake",
    )


# ---------------------------------------------------------------------
# Module cv : constructeurs de brouillons
# ---------------------------------------------------------------------

CV_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
CV_IDENTITY: dict[str, Any] = {
    "first_name": "Awa",
    "last_name": "Diallo",
    "headline": "Conseillère de vente",
}
CV_CONTACT: dict[str, Any] = {
    "email": "awa@example.com",
    "phone": None,
    "city": "Montreuil",
    "links": [],
}
CV_EXPERIENCE: dict[str, Any] = {
    "position": "Vendeuse",
    "company": "Zara",
    "missions": ["Conseiller"],
}


def complete_draft(user_id: UUID | None = None, *, image_id: UUID | None = None) -> Draft:
    """Un brouillon où tout est validé ou passé."""
    draft = Draft.new(user_id or uuid4(), now=CV_NOW)
    draft.submit(Chapter.IDENTITY, ChapterSubmission(data=CV_IDENTITY), now=CV_NOW)
    draft.submit(Chapter.CONTACT, ChapterSubmission(data=CV_CONTACT), now=CV_NOW)
    draft.submit(
        Chapter.SUMMARY, ChapterSubmission(data={"summary": "Je suis souriante."}), now=CV_NOW
    )
    draft.submit(Chapter.EXPERIENCES, ChapterSubmission(items=[CV_EXPERIENCE]), now=CV_NOW)
    draft.submit(Chapter.EDUCATIONS, ChapterSubmission(skipped=True), now=CV_NOW)
    draft.submit(
        Chapter.SKILLS,
        ChapterSubmission(
            data={
                "skills": [{"name": "Vente", "level": "Avancé"}],
                "languages": [{"language": "Anglais", "level": "C1"}],
            }
        ),
        now=CV_NOW,
    )
    draft.submit(
        Chapter.INTERESTS,
        ChapterSubmission(data={"interests": [{"name": "Danse", "category": "Sport"}]}),
        now=CV_NOW,
    )
    draft.submit(
        Chapter.PHOTO,
        ChapterSubmission(data={"image_id": str(image_id) if image_id else None}),
        now=CV_NOW,
    )
    return draft


# ---------------------------------------------------------------------
# Module `support`
# ---------------------------------------------------------------------


class InMemoryConversationRepository:
    """Implémentation en mémoire du port `ConversationRepository`."""

    def __init__(self) -> None:
        self.rows: dict[UUID, SupportConversation] = {}

    async def find_by_user(self, user_id: UUID) -> SupportConversation | None:
        return next((c for c in self.rows.values() if c.user_id == user_id), None)

    async def find(self, conversation_id: UUID) -> SupportConversation | None:
        return self.rows.get(conversation_id)

    async def add(self, conversation: SupportConversation) -> None:
        self.rows[conversation.id] = conversation

    async def save(self, conversation: SupportConversation) -> None:
        self.rows[conversation.id] = conversation

    def _filtered(self, awaiting_only: bool) -> list[SupportConversation]:
        rows = [c for c in self.rows.values() if not awaiting_only or c.awaits_team]
        return sorted(rows, key=lambda c: c.updated_at, reverse=True)

    async def list_for_team(
        self, *, limit: int, offset: int, awaiting_only: bool
    ) -> list[SupportConversationListing]:
        self.messages: InMemorySupportMessageRepository
        listings = []
        for c in self._filtered(awaiting_only)[offset : offset + limit]:
            mine = [m for m in self.messages.rows if m.conversation_id == c.id]
            listings.append(
                SupportConversationListing(
                    conversation=c,
                    last_message=max(mine, key=lambda m: m.created_at, default=None),
                    message_count=len(mine),
                )
            )
        return listings

    async def count_for_team(self, *, awaiting_only: bool) -> int:
        return len(self._filtered(awaiting_only))

    async def delete_by_user(self, user_id: UUID) -> int:
        doomed = [c.id for c in self.rows.values() if c.user_id == user_id]
        for cid in doomed:
            del self.rows[cid]
        return len(doomed)


class InMemorySupportMessageRepository:
    """Implémentation en mémoire du port `MessageRepository`."""

    def __init__(self) -> None:
        self.rows: list[SupportMessage] = []

    async def add(self, message: SupportMessage) -> None:
        self.rows.append(message)

    async def list_recent(self, conversation_id: UUID, *, limit: int) -> list[SupportMessage]:
        mine = sorted(
            (m for m in self.rows if m.conversation_id == conversation_id),
            key=lambda m: m.created_at,
        )
        return mine[-limit:]

    async def list_after(self, conversation_id: UUID, *, after: datetime) -> list[SupportMessage]:
        return sorted(
            (m for m in self.rows if m.conversation_id == conversation_id and m.created_at > after),
            key=lambda m: m.created_at,
        )

    async def attachment_keys(self, conversation_id: UUID) -> list[str]:
        return [
            m.attachment.key
            for m in self.rows
            if m.conversation_id == conversation_id and m.attachment is not None
        ]


class RecordingUserNotifier:
    """Port `UserNotifier` : garde ce qui aurait été envoyé."""

    def __init__(self) -> None:
        self.sent: list[tuple[UUID, PushMessage]] = []
        self.fail_with: Exception | None = None

    async def notify(self, user_id: UUID, message: PushMessage) -> int:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((user_id, message))
        return 1


class RecordingTeamAlerter:
    """Port `TeamAlerter` : garde les alertes."""

    def __init__(self) -> None:
        self.alerts: list[dict[str, Any]] = []

    async def alert(
        self, *, conversation_id: UUID, user_email: str | None, preview: str, kind: Any
    ) -> None:
        self.alerts.append(
            {
                "conversation_id": conversation_id,
                "user_email": user_email,
                "preview": preview,
                "kind": kind,
            }
        )


class StaticAccountDirectory:
    """Port `AccountDirectory` : la même adresse pour tout compte connu."""

    def __init__(self, email: str | None = "awa@exemple.fr") -> None:
        self.email = email

    async def email_of(self, user_id: UUID) -> str | None:
        _ = user_id
        return self.email


class RecordingFeed:
    """Port `ConversationFeed` : garde les réveils, et réveille un vrai bus si on lui en donne un."""

    def __init__(self, broker: InMemoryBroker | None = None) -> None:
        self.woken: list[UUID] = []
        self._broker = broker

    async def wake(self, conversation_id: UUID) -> None:
        self.woken.append(conversation_id)
        if self._broker is not None:
            await self._broker.publish(f"support:{conversation_id}")


@dataclass(slots=True)
class SupportEnv:
    """Toutes les doublures du module `support`, câblées ensemble."""

    conversations: InMemoryConversationRepository
    messages: InMemorySupportMessageRepository
    notifier: RecordingUserNotifier
    alerter: RecordingTeamAlerter
    directory: StaticAccountDirectory
    feed: RecordingFeed
    clock: FakeClock

    @classmethod
    def build(cls, broker: InMemoryBroker | None = None) -> SupportEnv:
        conversations = InMemoryConversationRepository()
        messages = InMemorySupportMessageRepository()
        conversations.messages = messages
        return cls(
            conversations=conversations,
            messages=messages,
            notifier=RecordingUserNotifier(),
            alerter=RecordingTeamAlerter(),
            directory=StaticAccountDirectory(),
            feed=RecordingFeed(broker),
            clock=FakeClock(),
        )
