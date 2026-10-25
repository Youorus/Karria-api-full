"""Adaptateurs du module `offers` : Tortoise, Qdrant, TEI, port LLM, BAN, stockage, file."""

from karria.modules.offers.infrastructure.companies import TortoiseCompanyRepository
from karria.modules.offers.infrastructure.dialogue import LlmDialogue
from karria.modules.offers.infrastructure.geocoding import BanGeocoder, CachedGeocoder
from karria.modules.offers.infrastructure.inference import TeiEmbedder
from karria.modules.offers.infrastructure.landing import ObjectStorageLandingStore
from karria.modules.offers.infrastructure.logos import (
    HttpLogoFetcher,
    PillowLogoProcessor,
    StorageLogoStore,
)
from karria.modules.offers.infrastructure.memory_updater import LlmMemoryUpdater
from karria.modules.offers.infrastructure.qdrant import (
    QdrantCriteriaIndex,
    QdrantOfferIndex,
    build_transport,
    collection_name,
)
from karria.modules.offers.infrastructure.queue import ProcrastinateJobQueue
from karria.modules.offers.infrastructure.repositories import (
    TortoiseConversationRepository,
    TortoiseCriteriaRepository,
    TortoiseFeedRepository,
    TortoiseGeocodeCache,
    TortoiseIndexOutbox,
    TortoiseIngestionRunRepository,
    TortoiseInteractionLog,
    TortoiseKarriaMemoryRepository,
    TortoiseOfferRepository,
    TortoiseOfferStateRepository,
    TortoiseScrapeTargetRepository,
)

__all__ = [
    "BanGeocoder",
    "CachedGeocoder",
    "HttpLogoFetcher",
    "LlmDialogue",
    "LlmMemoryUpdater",
    "ObjectStorageLandingStore",
    "PillowLogoProcessor",
    "ProcrastinateJobQueue",
    "QdrantCriteriaIndex",
    "QdrantOfferIndex",
    "StorageLogoStore",
    "TeiEmbedder",
    "TortoiseCompanyRepository",
    "TortoiseConversationRepository",
    "TortoiseCriteriaRepository",
    "TortoiseFeedRepository",
    "TortoiseGeocodeCache",
    "TortoiseIndexOutbox",
    "TortoiseIngestionRunRepository",
    "TortoiseInteractionLog",
    "TortoiseKarriaMemoryRepository",
    "TortoiseOfferRepository",
    "TortoiseOfferStateRepository",
    "TortoiseScrapeTargetRepository",
    "build_transport",
    "collection_name",
]
