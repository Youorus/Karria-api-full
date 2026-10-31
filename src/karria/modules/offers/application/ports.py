"""Ports du module `offers` : chaque besoin des cas d'usage, sans dire qui y répond.

Les dépôts parlent le vocabulaire du domaine (`Offer`, `FeedEntry`,
`SearchCriteria`) et ne rendent jamais un modèle de persistance. Les
services extérieurs — index vectoriel, plongements, reclassement, modèle de
langage, géocodeur, source d'annonces — sont décrits par ce qu'on leur
demande, pas par leur produit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from karria.modules.offers.domain import (
    Candidate,
    ContractType,
    DialogueProposal,
    FeedEntry,
    FeedSort,
    GeocodedPlace,
    InteractionEvent,
    KarriaMemory,
    MemoryFacts,
    Offer,
    OfferState,
    ScrapeTarget,
    SearchCriteria,
    TargetDemand,
    TargetOrigin,
    TargetStats,
    Widening,
)
from karria.modules.offers.domain.companies import CompanyRecord, ImageTraits
from karria.shared.push import PushMessage

# ---------------------------------------------------------------------
# Le corpus
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OfferFilters:
    """Filtres d'une page du fil ou d'une recherche."""

    contract_types: tuple[ContractType, ...] = ()
    cities: tuple[str, ...] = ()
    published_after: datetime | None = None
    #: Offres à ne pas montrer : écartées, ou déjà vues quand on ne veut que du neuf.
    exclude_offer_ids: tuple[UUID, ...] = ()


class OfferRepository(Protocol):
    """Port : la vérité sur les offres, dans PostgreSQL.

    Les méthodes d'ingestion travaillent **par lot** : 50 000 à 70 000
    annonces par jour ne se traitent pas en cinq allers-retours chacune.
    """

    async def insert_many(self, offers: Sequence[Offer]) -> None:
        """Enregistre des offres nouvelles.

        Une annonce qu'un lot **concurrent** vient d'écrire (même source,
        même identifiant — le balayage nocturne et le passage de trois
        minutes se recouvrent) est laissée telle quelle : le passage suivant
        la revoit et la met à jour si son contenu diffère. Avant, le lot
        entier échouait et se rejouait (`UniqueViolation`, 2026-10-28).
        """
        ...

    async def save(self, offer: Offer) -> None:
        """Écrit l'état complet d'une offre existante."""
        ...

    async def find(self, offer_id: UUID) -> Offer | None:
        """Une offre par identifiant, active ou non."""
        ...

    async def find_many(
        self, offer_ids: Sequence[UUID], *, light: bool = False
    ) -> dict[UUID, Offer]:
        """Plusieurs offres par identifiant, en un aller-retour.

        `light` : sans la description (vide), pour ce qui ne la lit pas — le
        filtre dur, la porte d'intitulé, le comptage des élargissements. Un
        rappel de 1 500 candidats pèse 3 Mo de descriptions contre 60 Ko sans
        elles (mesuré en production le 2026-10-29) ; un tour de conversation
        en fait jusqu'à neuf.
        """
        ...

    async def find_by_sources(
        self, keys: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], Offer]:
        """Les offres déjà connues sous ces couples (source, identifiant de source)."""
        ...

    async def find_canonical_by_hashes(self, hashes: Sequence[str]) -> dict[str, Offer]:
        """L'offre canonique **active** de chaque condensat, quand elle existe."""
        ...

    async def near_duplicate_candidates(
        self, *, bands: tuple[int, int, int, int], company_key: str | None, since: datetime
    ) -> Sequence[Offer]:
        """Les offres canoniques actives récentes qui partagent une bande SimHash."""
        ...

    async def mark_seen(self, offer_ids: Sequence[UUID], *, at: datetime) -> None:
        """Rafraîchit `last_seen_at` : l'offre est encore en ligne."""
        ...

    async def page_active(
        self,
        *,
        filters: OfferFilters,
        limit: int,
        offset: int,
        near: tuple[float, float] | None = None,
    ) -> tuple[Sequence[Offer], int]:
        """Les offres actives canoniques et leur total.

        Les plus récentes d'abord ; avec `near` (latitude, longitude), les plus
        proches d'abord, les offres sans coordonnées en dernier, puis la date et
        l'identifiant pour que la pagination reste stable.
        """
        ...

    async def mark_indexed(
        self, indexed: Sequence[tuple[UUID, str]], *, version: str, at: datetime
    ) -> None:
        """Note que l'index porte ces offres, plongées sur ce condensat à cette version."""
        ...

    async def mark_unindexed(self, offer_ids: Sequence[UUID]) -> None:
        """Note que l'index ne porte plus ces offres."""
        ...

    async def deactivate_published_before(self, moment: datetime, *, at: datetime) -> list[UUID]:
        """Désactive les offres trop anciennes. Rend les identifiants touchés."""
        ...

    async def stale_index_ids(self, *, version: str, limit: int) -> list[UUID]:
        """Les offres dont l'index n'est pas à jour : à indexer, ou à retirer."""
        ...

    async def count_indexable(self) -> int:
        """Combien d'offres l'index devrait porter."""
        ...

    async def filter_options(
        self, offer_ids: Sequence[UUID] | None
    ) -> tuple[dict[ContractType, int], dict[str, int]]:
        """Contrats et villes présents parmi ces offres (toutes les actives si `None`)."""
        ...


@dataclass(frozen=True, slots=True)
class OutboxItem:
    """Une offre à resynchroniser avec l'index."""

    id: int
    offer_id: UUID


class IndexOutbox(Protocol):
    """Port : la boîte d'envoi transactionnelle vers l'index.

    Une ligne s'écrit **dans la transaction** qui change l'offre : si
    l'écriture de l'offre est validée, la resynchronisation l'est aussi ;
    si elle est annulée, rien ne part. La ligne ne dit pas quoi faire :
    l'opération (plonger, mettre à jour les attributs, retirer) se déduit
    de l'état de l'offre au moment du traitement, ce qui rend le
    traitement idempotent et l'ordre des lignes indifférent.
    """

    async def enqueue(self, offer_ids: Sequence[UUID], *, at: datetime) -> None:
        """Inscrit ces offres à resynchroniser."""
        ...

    async def claim(self, *, limit: int) -> Sequence[OutboxItem]:
        """Les plus anciennes lignes en attente (un seul consommateur à la fois)."""
        ...

    async def ack(self, item_ids: Sequence[int]) -> None:
        """Retire les lignes traitées."""
        ...

    async def pending(self) -> int:
        """Combien de lignes attendent."""
        ...


@dataclass(frozen=True, slots=True)
class IndexedOffer:
    """Ce qui entre dans l'index pour une offre : son vecteur, ses textes lexicaux, ses attributs."""

    offer: Offer
    dense: tuple[float, ...]
    title_text: str
    body_text: str


@dataclass(frozen=True, slots=True)
class IndexQuery:
    """Une interrogation hybride de l'index."""

    dense: tuple[float, ...]
    #: Les intitulés visés, pour la branche lexicale d'intitulé.
    title_terms: str
    #: Intitulés et mots-clés, pour la branche lexicale de corps.
    body_terms: str
    criteria: SearchCriteria
    limit: int
    offset: int = 0


class OfferIndex(Protocol):
    """Port : l'index de recherche (dense + lexical, filtres)."""

    async def ensure_ready(self) -> None:
        """Crée la collection et ses index si besoin. Idempotent."""
        ...

    async def upsert(self, items: Sequence[IndexedOffer]) -> None:
        """Écrit ou remplace ces offres dans l'index (vecteurs et attributs)."""
        ...

    async def update_payloads(self, offers: Sequence[Offer]) -> None:
        """Réécrit les attributs filtrables, sans toucher aux vecteurs."""
        ...

    async def delete(self, offer_ids: Sequence[UUID]) -> None:
        """Retire ces offres de l'index."""
        ...

    async def search(self, query: IndexQuery) -> Sequence[Candidate]:
        """Les candidats, fusion des branches dense et lexicales, filtres appliqués."""
        ...

    async def title_candidates(self, term: str, *, limit: int) -> Sequence[UUID]:
        """Les offres actives dont le **titre** répond lexicalement à ce terme.

        Sert à vérifier qu'un terme de collecte désigne un métier que des
        annonces titrent vraiment (`domain/steering.py::validate_terms`).
        """
        ...

    async def count(self) -> int:
        """Combien d'offres l'index porte — pour la réconciliation."""
        ...


@dataclass(frozen=True, slots=True)
class CriteriaMatch:
    """Un jeu de critères proche d'une offre."""

    user_id: UUID
    similarity: float


class CriteriaIndex(Protocol):
    """Port : l'index des critères, pour le matching inversé des alertes."""

    async def ensure_ready(self) -> None:
        """Crée la collection si besoin. Idempotent."""
        ...

    async def upsert(
        self,
        user_id: UUID,
        dense: Sequence[float],
        criteria: SearchCriteria,
        *,
        alerts_enabled: bool,
    ) -> None:
        """Écrit le vecteur des critères d'une personne, et de quoi filtrer par lieu."""
        ...

    async def delete(self, user_id: UUID) -> None:
        """Retire les critères d'une personne."""
        ...

    async def set_alerts_enabled(self, user_id: UUID, *, enabled: bool) -> None:
        """Lève ou baisse le drapeau des alertes, sans toucher au vecteur."""
        ...

    async def similar(
        self, dense: Sequence[float], offer: Offer, *, limit: int, min_similarity: float
    ) -> Sequence[CriteriaMatch]:
        """Les personnes dont les critères ressemblent à cette offre et couvrent son lieu.

        Alertes actives seulement. Le filtre de lieu est **large** (le
        département, ou un cercle de rayon maximal) ; le domaine tranche
        ensuite avec `SearchCriteria.accepts`.
        """
        ...


# ---------------------------------------------------------------------
# Les modèles
# ---------------------------------------------------------------------


class TextEmbedder(Protocol):
    """Port : des textes aux vecteurs denses."""

    @property
    def version(self) -> str:
        """Identifie le modèle : entre dans la version d'index."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        """Plonge des documents (annonces)."""
        ...

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Plonge une requête (la demande d'une personne)."""
        ...


class SearchDialogue(Protocol):
    """Port : le modèle de langage qui comprend la demande et répond."""

    async def propose(
        self,
        *,
        history: Sequence[tuple[str, str]],
        current: SearchCriteria | None,
        message: str,
        memory: str | None = None,
        facts: str | None = None,
    ) -> DialogueProposal:
        """Une réponse et des critères, à partir de l'échange et de la mémoire de la personne.

        `facts` : des données comptées par l'application (les élargissements
        proposés au tour précédent), que le modèle relit sans les modifier.

        Raises:
            ModelUnavailableError: Le fournisseur ne répond pas.
            ModelOutputInvalidError: La sortie n'a pas la forme imposée.
        """
        ...


# ---------------------------------------------------------------------
# La collecte
# ---------------------------------------------------------------------


class MemoryUpdater(Protocol):
    """Port : le modèle qui relit un tour et propose ce que Karria doit retenir de la personne."""

    async def propose(
        self, *, current: MemoryFacts, user_message: str, reply: str
    ) -> MemoryFacts | None:
        """Les faits à retenir après ce tour, ou `None` si rien de nouveau (ou refus).

        Raises:
            ModelUnavailableError: Le fournisseur ne répond pas.
            ModelOutputInvalidError: La sortie n'a pas la forme imposée.
        """
        ...


class KarriaMemoryRepository(Protocol):
    """Port : la mémoire que Karria garde de chaque personne."""

    async def find(self, user_id: UUID) -> KarriaMemory | None:
        """La mémoire, ou `None` si rien n'a été retenu."""
        ...

    async def save(self, user_id: UUID, memory: KarriaMemory) -> None:
        """Crée ou remplace."""
        ...

    async def delete(self, user_id: UUID) -> bool:
        """Oublie. `False` s'il n'y avait rien."""
        ...


class Geocoder(Protocol):
    """Port : d'un libellé de lieu à des coordonnées."""

    async def geocode(self, query: str) -> GeocodedPlace | None:
        """Le lieu, ou `None` si le géocodeur ne le reconnaît pas."""
        ...


class LandingStore(Protocol):
    """Port : l'atterrissage des lots bruts remis par le collecteur.

    Le lot est gardé tel qu'il est arrivé (JSONL gzip) : c'est lui qu'on
    rejoue quand une règle de normalisation change.
    """

    async def store(self, batch_id: UUID, payload: bytes, *, at: datetime) -> str:
        """Écrit le lot et rend la clé de l'objet."""
        ...

    async def read(self, key: str) -> list[dict[str, Any]]:
        """Relit un lot : une entrée par ligne JSON. Les lignes illisibles sont sautées."""
        ...


@dataclass(frozen=True, slots=True)
class StoredTarget:
    """Une cible de collecte telle qu'elle est persistée : la requête, le calendrier, l'appris."""

    key: str
    target: ScrapeTarget
    next_scrape_at: datetime | None
    last_scraped_at: datetime | None
    stats: TargetStats = field(default_factory=TargetStats)
    #: Dernière recherche maigre qui l'a demandée (le « manque »).
    thin_since: datetime | None = None
    is_active: bool = True


class ScrapeTargetRepository(Protocol):
    """Port : le catalogue des cibles de collecte, leur calendrier, la demande qui les porte."""

    async def upsert_many(
        self, targets: Sequence[ScrapeTarget], *, at: datetime, due_now: bool = False
    ) -> None:
        """Crée les cibles nouvelles (à l'essai), met à jour le poids des connues, les réactive.

        `due_now` avance leur prochaine collecte à maintenant et note le
        manque : une recherche qui trouve peu d'offres demande sa collecte
        sans attendre.
        """
        ...

    async def due(self, *, now: datetime, limit: int) -> Sequence[StoredTarget]:
        """Les cibles actives échues, candidates au prochain passage (ordre indifférent)."""
        ...

    async def lease(self, keys: Sequence[str], *, until: datetime) -> None:
        """Repousse leur prochaine collecte : elles sont confiées au collecteur."""
        ...

    async def find(self, key: str) -> StoredTarget | None:
        """Une cible par sa clé."""
        ...

    async def record_scrape(
        self, key: str, stats: TargetStats, *, at: datetime, next_at: datetime
    ) -> None:
        """Note le passage, ce qu'il a appris, et planifie le suivant."""
        ...

    async def replace_demands(self, user_id: UUID, keys: Sequence[str], *, at: datetime) -> None:
        """La personne demande désormais ces cibles, et plus les autres."""
        ...

    async def all_demands(self) -> Sequence[TargetDemand]:
        """Qui demande quoi, depuis quand — pour peser la demande."""
        ...

    async def set_weights(self, weights: Mapping[str, int]) -> None:
        """Réécrit le nombre de personnes derrière chaque cible."""
        ...

    async def deactivate_demand_before(self, moment: datetime) -> int:
        """Éteint les cibles demandées que plus personne n'a redemandées depuis."""
        ...

    async def count_active(self, origin: TargetOrigin | None = None) -> int:
        """Combien de cibles actives."""
        ...

    async def catalogue(self, *, limit: int) -> Sequence[StoredTarget]:
        """Les cibles actives, les plus demandées d'abord — pour l'exploitation."""
        ...


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """Ce qu'un passage de collecte a produit."""

    run_id: UUID
    started_at: datetime
    finished_at: datetime
    targets: int = 0
    fetched: int = 0
    inserted: int = 0
    refreshed: int = 0
    duplicates: int = 0
    near_duplicates: int = 0
    errors: int = 0
    updated: int = 0
    landing_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def new_share(self) -> float:
        """La part du lot réellement nouvelle — ce qui sera plongé."""
        return self.inserted / self.fetched if self.fetched else 0.0


class IngestionRunRepository(Protocol):
    """Port : la trace des passages de collecte."""

    async def record(self, report: IngestionReport) -> None:
        """Conserve le bilan d'un passage."""
        ...


# ---------------------------------------------------------------------
# La personne
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredCriteria:
    """Les critères d'une personne et l'état de ses alertes."""

    user_id: UUID
    criteria: SearchCriteria
    fingerprint: str
    alerts_enabled: bool
    updated_at: datetime
    last_alert_at: datetime | None = None
    alerts_sent_on: datetime | None = None
    alerts_sent_today: int = 0


class CriteriaRepository(Protocol):
    """Port : les critères courants de chaque personne."""

    async def find(self, user_id: UUID) -> StoredCriteria | None:
        """Les critères d'une personne, ou `None`."""
        ...

    async def save(self, stored: StoredCriteria) -> None:
        """Crée ou remplace."""
        ...

    async def delete(self, user_id: UUID) -> bool:
        """Oublie les critères. `False` s'il n'y en avait pas."""
        ...

    async def all_with_alerts(self) -> Sequence[StoredCriteria]:
        """Tous les critères actifs — pour piloter la collecte."""
        ...

    async def find_many(self, user_ids: Sequence[UUID]) -> dict[UUID, StoredCriteria]:
        """Les critères de plusieurs personnes."""
        ...

    async def record_alert(self, user_id: UUID, *, at: datetime) -> None:
        """Note qu'une notification est partie maintenant."""
        ...


@dataclass(frozen=True, slots=True)
class Message:
    """Un message d'une conversation."""

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    created_at: datetime


class InteractionLog(Protocol):
    """Port : le journal d'apprentissage (ADR-0019), en ajout seulement."""

    async def append(self, events: Sequence[InteractionEvent]) -> None:
        """Consigne ces gestes."""
        ...


class ConversationRepository(Protocol):
    """Port : les conversations avec Karria."""

    async def create(self, user_id: UUID, *, at: datetime) -> UUID:
        """Ouvre une conversation et rend son identifiant."""
        ...

    async def belongs_to(self, conversation_id: UUID, user_id: UUID) -> bool:
        """La conversation existe-t-elle pour cette personne ?"""
        ...

    async def add_message(
        self, conversation_id: UUID, *, role: str, content: str, at: datetime
    ) -> Message:
        """Ajoute un message et le rend."""
        ...

    async def recent_messages(self, conversation_id: UUID, *, limit: int) -> Sequence[Message]:
        """Les derniers messages, du plus ancien au plus récent."""
        ...

    async def set_pending_widenings(
        self, conversation_id: UUID, widenings: Sequence[Widening]
    ) -> None:
        """Garde les élargissements proposés à ce tour (vide : plus rien en attente)."""
        ...

    async def pending_widenings(self, conversation_id: UUID) -> Sequence[Widening]:
        """Les élargissements proposés au dernier tour de recherche."""
        ...

    async def user_turns_since(self, user_id: UUID, since: datetime) -> int:
        """Combien de messages la personne a envoyés depuis cet instant."""
        ...

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface les conversations d'une personne. Rend le nombre."""
        ...


@dataclass(frozen=True, slots=True)
class FeedQuery:
    """Une page du fil."""

    user_id: UUID
    limit: int
    offset: int
    sort: FeedSort = FeedSort.RELEVANCE
    saved_only: bool = False
    unseen_only: bool = False
    filters: OfferFilters = field(default_factory=OfferFilters)
    #: Le point de référence de `FeedSort.NEAREST` (latitude, longitude) ; ignoré sinon.
    near: tuple[float, float] | None = None


class FeedRepository(Protocol):
    """Port : les entrées de fil."""

    async def entries_of(self, user_id: UUID) -> Sequence[FeedEntry]:
        """Toutes les entrées d'une personne."""
        ...

    async def replace(
        self, user_id: UUID, entries: Sequence[FeedEntry], *, remove: Sequence[UUID]
    ) -> None:
        """Écrit ces entrées (création ou mise à jour) et retire celles-là."""
        ...

    async def add_many(self, entries: Sequence[FeedEntry]) -> None:
        """Ajoute des entrées (alertes) sans toucher aux existantes."""
        ...

    async def page(self, query: FeedQuery) -> tuple[Sequence[FeedEntry], int]:
        """Une page d'entrées, triée, filtrée, avec le total."""
        ...

    async def find(self, user_id: UUID, offer_id: UUID) -> FeedEntry | None:
        """L'entrée de cette offre pour cette personne."""
        ...

    async def mark_notified(
        self, user_id: UUID, offer_ids: Sequence[UUID], *, at: datetime
    ) -> None:
        """Note que ces entrées ont fait l'objet d'une notification."""
        ...

    async def offer_ids_of(self, user_id: UUID) -> Sequence[UUID]:
        """Les identifiants d'offres du fil, pour les options de filtre."""
        ...

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface le fil d'une personne."""
        ...


class OfferStateRepository(Protocol):
    """Port : ce que chaque personne a fait de chaque offre."""

    async def find(self, user_id: UUID, offer_id: UUID) -> OfferState | None:
        """L'état, ou `None` si rien n'a jamais été fait."""
        ...

    async def find_many(self, user_id: UUID, offer_ids: Sequence[UUID]) -> dict[UUID, OfferState]:
        """Les états de plusieurs offres."""
        ...

    async def all_of_user(self, user_id: UUID) -> dict[UUID, OfferState]:
        """Tous les états d'une personne."""
        ...

    async def offer_ids_where(
        self,
        user_id: UUID,
        *,
        saved: bool = False,
        dismissed: bool = False,
        seen: bool = False,
        applied: bool = False,
    ) -> Sequence[UUID]:
        """Les offres pour lesquelles **au moins un** des indicateurs demandés est vrai."""
        ...

    async def save(self, state: OfferState) -> None:
        """Crée ou remplace."""
        ...

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface les états d'une personne."""
        ...


class JobQueue(Protocol):
    """Port : différer un travail dans la file durable (procrastinate, sur PostgreSQL)."""

    async def defer_index_sync(self) -> None:
        """Demande une resynchronisation de l'index (dédoublonnée par la file)."""
        ...

    async def defer_ingest_batch(self, key: str, target_key: str | None) -> None:
        """Demande d'ingérer un lot remis par le collecteur."""
        ...

    async def defer_memory_update(self, user_id: UUID, *, user_message: str, reply: str) -> None:
        """Demande de mettre à jour la mémoire de la personne après ce tour, hors du chemin de la réponse."""
        ...

    async def defer_logo_fetch(self) -> None:
        """Demande un passage de recherche de logos (un seul en attente à la fois)."""
        ...


class UserNotifier(Protocol):
    """Port : prévenir une personne. Branché sur `devices` par `bootstrap/wiring.py`."""

    async def notify(self, user_id: UUID, message: PushMessage) -> int:
        """Envoie à tous les appareils joignables. Rend le nombre accepté."""
        ...


# ---------------------------------------------------------------------
# Les employeurs et leurs logos
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OfferLink:
    """Une offre à rattacher à son entreprise : (source, identifiant) → entreprise."""

    source: str
    source_id: str
    company_id: UUID


class CompanyRepository(Protocol):
    """Port : les entreprises (`offers_companies`) et leur lien aux offres."""

    async def find_by_keys(self, keys: Sequence[str]) -> dict[str, list[CompanyRecord]]:
        """Les entreprises connues sous chaque clé de nom, la plus ancienne d'abord."""
        ...

    async def save_many(self, records: Sequence[CompanyRecord]) -> Mapping[UUID, UUID]:
        """Crée ou met à jour (par identifiant).

        Rend, pour chaque enregistrement, l'identifiant **réellement** porté
        en base : si un autre lot a créé la même entreprise (même clé, même
        domaine) entre la lecture et l'écriture, c'est le sien qui gagne, et
        l'appelant rattache ses offres à celui-là. Deux lots du balayage
        nocturne l'ont fait le 2026-10-27 (`UniqueViolation (key, domain)`).
        """
        ...

    async def link_offers(self, links: Sequence[OfferLink]) -> int:
        """Rattache les offres et y recopie le logo de l'entreprise. Rend le nombre modifié."""
        ...

    async def claim_logo_work(
        self, *, now: datetime, until: datetime, limit: int
    ) -> list[CompanyRecord]:
        """Prend les entreprises dont le logo est à chercher maintenant, celles qui ont des offres
        actives d'abord, et les met sous bail jusqu'à `until` (sans double prise)."""
        ...

    async def save_logo(self, record: CompanyRecord) -> int:
        """Enregistre l'issue d'une recherche et recopie le logo sur ses offres. Rend leur nombre."""
        ...

    async def domain_share(self, domain: str) -> int:
        """Combien d'entreprises portent ce domaine."""
        ...

    async def purge_shared_logos(self, limit: int) -> int:
        """Retire les logos de la V1 ou d'icône portés par plus de `limit` entreprises.

        Rend le nombre d'entreprises remises « sans logo » (et leurs offres avec).
        """
        ...

    async def unlinked_offers(
        self, *, after: UUID | None, limit: int, active_only: bool
    ) -> list[Offer]:
        """Les offres qui nomment un employeur sans y être rattachées, par identifiant croissant
        après `after` (rattrapage)."""
        ...


@dataclass(frozen=True, slots=True)
class FetchedImage:
    """Ce qu'un téléchargement a rendu."""

    #: Les octets, ou `None` si rien n'est venu.
    payload: bytes | None
    #: L'échec est définitif (404, pas une image, trop gros) : inutile de réessayer.
    permanent: bool = False


class LogoFetcher(Protocol):
    """Port : aller chercher une image chez un tiers, poliment."""

    async def fetch_image(self, url: str) -> FetchedImage:
        """Télécharge une image (délais, taille et débit bornés)."""
        ...

    async def site_icons(self, domain: str) -> list[str]:
        """Les icônes que déclare la page d'accueil du domaine, la plus grande d'abord."""
        ...


@dataclass(frozen=True, slots=True)
class ProcessedLogo:
    """Une image mise au carré, mesurée, déclinée dans chaque taille servie."""

    digest: str
    traits: ImageTraits
    #: Taille → octets WebP.
    renditions: Mapping[int, bytes]


class LogoProcessor(Protocol):
    """Port : normaliser une image en logo carré (rognage, fond, tailles)."""

    def process(self, payload: bytes) -> ProcessedLogo | None:
        """`None` si les octets ne sont pas une image lisible."""
        ...


class LogoStore(Protocol):
    """Port : le stock des logos normalisés, adressés par condensat."""

    async def has(self, digest: str) -> bool:
        """Le logo est déjà en stock (une autre entreprise a le même)."""
        ...

    async def put(self, logo: ProcessedLogo) -> None:
        """Écrit chaque taille ; idempotent."""
        ...
