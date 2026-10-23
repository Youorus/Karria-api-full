"""Modèles de persistance du module `offers`.

La table des offres est la **vérité** ; l'index vectoriel n'en est qu'une
projection, reconstructible (`indexed_version`, `indexed_at`). Aucune clé
étrangère vers les comptes : `user_id` est un UUID opaque (règle 4).
"""

from __future__ import annotations

from typing import Any

from tortoise import fields
from tortoise.models import Model

from karria.modules.offers.domain import (
    ContractType,
    ExperienceLevel,
    FeedOrigin,
    MatchQuality,
    RemoteMode,
    Schedule,
    Source,
    TargetOrigin,
    TargetState,
)
from karria.shared.database import BaseDBModel

URL_MAX_LENGTH = 2_048
LABEL_MAX_LENGTH = 255
HASH_LENGTH = 64


class OfferModel(BaseDBModel):
    """Une offre normalisée."""

    source = fields.CharEnumField(Source, max_length=16)
    source_id = fields.CharField(max_length=LABEL_MAX_LENGTH)
    title = fields.CharField(max_length=LABEL_MAX_LENGTH)
    company_name = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    company_key = fields.CharField(
        max_length=LABEL_MAX_LENGTH,
        null=True,
        description="Nom de l'employeur plié (minuscules, sans accents) : clé des quasi-doublons.",
    )
    company_logo_url = fields.CharField(max_length=URL_MAX_LENGTH, null=True)
    company_url = fields.CharField(max_length=URL_MAX_LENGTH, null=True)
    description = fields.TextField()
    location_label = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    city = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True, db_index=True)
    postal_code = fields.CharField(max_length=10, null=True)
    department_code = fields.CharField(max_length=3, null=True, db_index=True)
    latitude = fields.FloatField(null=True)
    longitude = fields.FloatField(null=True)
    contract_type = fields.CharEnumField(ContractType, max_length=16, null=True, db_index=True)
    remote = fields.CharEnumField(RemoteMode, max_length=16, null=True)
    schedule = fields.CharEnumField(Schedule, max_length=16, null=True)
    experience = fields.CharEnumField(ExperienceLevel, max_length=16, null=True)
    salary_min_monthly = fields.IntField(null=True)
    salary_max_monthly = fields.IntField(null=True)
    salary_raw = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    published_at = fields.DatetimeField()
    first_seen_at = fields.DatetimeField()
    last_seen_at = fields.DatetimeField()
    apply_url = fields.CharField(max_length=URL_MAX_LENGTH, null=True)
    origin_url = fields.CharField(max_length=URL_MAX_LENGTH, null=True)
    is_active = fields.BooleanField(default=True)
    deactivated_at = fields.DatetimeField(null=True)
    content_hash = fields.CharField(max_length=HASH_LENGTH, db_index=True)
    simhash = fields.BigIntField(description="Empreinte SimHash 64 bits, stockée signée.")
    simhash_band0 = fields.SmallIntField()
    simhash_band1 = fields.SmallIntField()
    simhash_band2 = fields.SmallIntField()
    simhash_band3 = fields.SmallIntField()
    duplicate_of = fields.UUIDField(
        null=True, db_index=True, description="Offre canonique dont celle-ci est un doublon."
    )
    indexed_version = fields.CharField(max_length=128, null=True)
    indexed_hash = fields.CharField(
        max_length=HASH_LENGTH,
        null=True,
        description="Condensat du contenu dont l'index porte le plongement.",
    )
    indexed_at = fields.DatetimeField(null=True)

    class Meta:
        table = "offers_offers"
        table_description = (
            "Offres d'emploi normalisées : la vérité, dont l'index est une projection. "
            "La ligne brute de la source vit dans le bucket `landing`, pas ici."
        )
        unique_together = (("source", "source_id"),)
        # Les quasi-doublons se cherchent par employeur ET par bande : un
        # index composite par bande sert la requête en lecture d'index.
        indexes = (
            ("is_active", "published_at"),
            ("company_key", "simhash_band0"),
            ("company_key", "simhash_band1"),
            ("company_key", "simhash_band2"),
            ("company_key", "simhash_band3"),
        )


class IndexOutboxModel(Model):
    """Une offre à resynchroniser avec l'index (boîte d'envoi transactionnelle).

    Clé entière croissante, à rebours de `BaseDBModel` : l'ordre d'arrivée
    est ce qu'on lit (`ORDER BY id`), et la table ne vit que quelques
    secondes par ligne.
    """

    id = fields.BigIntField(primary_key=True, generated=True)
    offer_id = fields.UUIDField()
    enqueued_at = fields.DatetimeField()

    class Meta:
        table = "offers_index_outbox"
        table_description = "Ce que l'index doit apprendre : écrit dans la transaction de l'offre."


class ScrapeTargetModel(BaseDBModel):
    """Une cible de collecte canonique : la requête, son calendrier, ce que les passages ont appris."""

    key = fields.CharField(max_length=512, unique=True)
    search_term = fields.CharField(max_length=LABEL_MAX_LENGTH)
    location = fields.CharField(max_length=LABEL_MAX_LENGTH)
    location_key = fields.CharField(
        max_length=8, default="fr", description="Département (« 75 », « 2A ») ou « fr »."
    )
    contract = fields.CharEnumField(ContractType, max_length=16, null=True)
    origin = fields.CharEnumField(TargetOrigin, max_length=16)
    state = fields.CharEnumField(TargetState, max_length=16, default=TargetState.PROBATION)
    weight = fields.IntField(default=1, description="Personnes derrière la cible.")
    is_active = fields.BooleanField(default=True)
    demanded_at = fields.DatetimeField(description="Dernière fois que la cible a été (re)demandée.")
    thin_since = fields.DatetimeField(
        null=True, description="Dernière recherche maigre qui l'a demandée."
    )
    distance_km = fields.IntField(null=True)
    last_scraped_at = fields.DatetimeField(null=True)
    next_scrape_at = fields.DatetimeField(null=True, db_index=True)
    last_found = fields.IntField(default=0)
    scrapes = fields.IntField(default=0)
    yield_ema = fields.FloatField(default=0.0, description="Offres nouvelles par passage, lissées.")
    zero_streak = fields.IntField(default=0)
    overlap_ema = fields.FloatField(
        default=0.0, description="Part des annonces déjà rapportées par France Travail, lissée."
    )
    gate_rejects = fields.IntField(default=0)

    class Meta:
        table = "offers_scrape_targets"
        table_description = (
            "Ce que la collecte demande aux sites d'offres, quand, et avec quel rendement."
        )


class TargetDemandModel(BaseDBModel):
    """Une personne demande une cible : le lien qui pèse la demande et porte le matching inversé."""

    user_id = fields.UUIDField()
    target_key = fields.CharField(max_length=512)
    demanded_at = fields.DatetimeField(
        description="Dernière recherche de la personne sur cette cible."
    )

    class Meta:
        table = "offers_target_demands"
        table_description = "Qui demande quelle cible de collecte, depuis quand."
        unique_together = (("user_id", "target_key"),)
        indexes = (("target_key",),)


class IngestionRunModel(BaseDBModel):
    """Le bilan d'un passage de collecte."""

    run_id = fields.UUIDField(unique=True)
    started_at = fields.DatetimeField()
    finished_at = fields.DatetimeField()
    targets = fields.IntField()
    fetched = fields.IntField()
    inserted = fields.IntField()
    refreshed = fields.IntField()
    duplicates = fields.IntField()
    near_duplicates = fields.IntField()
    updated = fields.IntField(default=0)
    errors = fields.IntField()
    landing_keys: list[str] = fields.JSONField()

    class Meta:
        table = "offers_ingestion_runs"
        table_description = "Trace des passages de collecte."


class GeocodeCacheModel(BaseDBModel):
    """Une requête de géocodage et sa réponse, pour ne pas la reposer."""

    query = fields.CharField(max_length=LABEL_MAX_LENGTH, unique=True)
    found = fields.BooleanField()
    city = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    postal_code = fields.CharField(max_length=10, null=True)
    department_code = fields.CharField(max_length=3, null=True)
    latitude = fields.FloatField(null=True)
    longitude = fields.FloatField(null=True)

    class Meta:
        table = "offers_geocode_cache"
        table_description = "Cache du géocodeur (Base Adresse Nationale)."


class CriteriaModel(BaseDBModel):
    """Les critères courants d'une personne."""

    user_id = fields.UUIDField(unique=True)
    criteria: dict[str, Any] = fields.JSONField(description="`SearchCriteria` sérialisés.")
    fingerprint = fields.CharField(max_length=32)
    alerts_enabled = fields.BooleanField(default=True)
    last_alert_at = fields.DatetimeField(null=True)
    alerts_sent_on = fields.DatetimeField(null=True)
    alerts_sent_today = fields.IntField(default=0)

    class Meta:
        table = "offers_criteria"
        table_description = "Ce que chaque personne cherche, et l'état de ses alertes."


class KarriaMemoryModel(BaseDBModel):
    """Ce que Karria a retenu d'une personne : des faits bornés, écrits par le modèle, fusionnés par la règle."""

    user_id = fields.UUIDField(unique=True)
    facts: dict[str, Any] = fields.JSONField(description="`MemoryFacts` sérialisés.")
    constitution_version = fields.CharField(max_length=16, default="")

    class Meta:
        table = "offers_karria_memory"
        table_description = (
            "La mémoire de Karria sur chaque personne (visible et effaçable par elle)."
        )


class ConversationModel(BaseDBModel):
    """Une conversation avec Karria."""

    user_id = fields.UUIDField(db_index=True)
    last_message_at = fields.DatetimeField()
    pending_widenings: list[dict[str, Any]] | None = fields.JSONField(
        null=True, description="Les élargissements proposés au dernier tour de recherche."
    )

    class Meta:
        table = "offers_conversations"
        table_description = "Conversations de recherche avec Karria."


class MessageModel(BaseDBModel):
    """Un message d'une conversation."""

    conversation_id = fields.UUIDField(db_index=True)
    user_id = fields.UUIDField(db_index=True)
    role = fields.CharField(max_length=16)
    content = fields.TextField()

    class Meta:
        table = "offers_messages"
        table_description = "Messages échangés avec Karria."


class FeedEntryModel(BaseDBModel):
    """Une offre retenue pour une personne."""

    user_id = fields.UUIDField()
    offer_id = fields.UUIDField()
    score = fields.FloatField()
    quality = fields.CharEnumField(MatchQuality, max_length=8)
    reasons: list[str] = fields.JSONField()
    origin = fields.CharEnumField(FeedOrigin, max_length=8)
    notified_at = fields.DatetimeField(null=True)

    class Meta:
        table = "offers_feed_entries"
        table_description = "Le fil de chaque personne."
        unique_together = (("user_id", "offer_id"),)
        indexes = (("user_id", "score"),)


class OfferStateModel(BaseDBModel):
    """Ce qu'une personne a fait d'une offre."""

    user_id = fields.UUIDField()
    offer_id = fields.UUIDField()
    seen = fields.BooleanField(default=False)
    opened = fields.BooleanField(default=False)
    link_clicked = fields.BooleanField(default=False)
    saved = fields.BooleanField(default=False)
    applied = fields.BooleanField(default=False)
    dismissed = fields.BooleanField(default=False)
    seen_at = fields.DatetimeField(null=True)
    opened_at = fields.DatetimeField(null=True)
    saved_at = fields.DatetimeField(null=True)
    applied_at = fields.DatetimeField(null=True)
    dismissed_at = fields.DatetimeField(null=True)

    class Meta:
        table = "offers_offer_states"
        table_description = (
            "Vue, ouverte, sauvegardée, candidatée, écartée : par personne et par offre."
        )
        unique_together = (("user_id", "offer_id"),)


class InteractionEventModel(BaseDBModel):
    """Un geste sur une offre, avec son instantané (ADR-0019). Ajout seulement."""

    user_id = fields.UUIDField(db_index=True)
    offer_id = fields.UUIDField(db_index=True)
    kind = fields.CharField(max_length=16)
    at = fields.DatetimeField()
    position = fields.IntField(null=True)
    dwell_ms = fields.IntField(null=True)
    feed_mode = fields.CharField(max_length=16)
    criteria_fingerprint = fields.CharField(max_length=32, null=True)
    score = fields.FloatField(null=True)
    quality = fields.CharField(max_length=8, null=True)
    origin = fields.CharField(max_length=8, null=True)
    source = fields.CharField(max_length=16)
    age_hours = fields.FloatField()
    distance_km = fields.FloatField(null=True)
    title_match = fields.SmallIntField(null=True)
    contract_match = fields.BooleanField(null=True)
    schedule_match = fields.BooleanField(null=True)
    has_salary = fields.BooleanField()

    class Meta:
        table = "offers_interaction_events"
        table_description = "Le journal d'apprentissage : gestes et contexte (ADR-0019)."
        indexes = (("at",),)
