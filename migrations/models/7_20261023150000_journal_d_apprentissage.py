from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "offers_interaction_events" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "offer_id" UUID NOT NULL,
    "kind" VARCHAR(16) NOT NULL,
    "at" TIMESTAMPTZ NOT NULL,
    "position" INT,
    "dwell_ms" INT,
    "feed_mode" VARCHAR(16) NOT NULL,
    "criteria_fingerprint" VARCHAR(32),
    "score" DOUBLE PRECISION,
    "quality" VARCHAR(8),
    "origin" VARCHAR(8),
    "source" VARCHAR(16) NOT NULL,
    "age_hours" DOUBLE PRECISION NOT NULL,
    "distance_km" DOUBLE PRECISION,
    "title_match" SMALLINT,
    "contract_match" BOOL,
    "schedule_match" BOOL,
    "has_salary" BOOL NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_offers_inte_user_id_a1e7c2" ON "offers_interaction_events" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_offers_inte_offer_i_b3d9f4" ON "offers_interaction_events" ("offer_id");
CREATE INDEX IF NOT EXISTS "idx_offers_inte_at_c5e1a8" ON "offers_interaction_events" ("at");
COMMENT ON COLUMN "offers_interaction_events"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_interaction_events"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "offers_interaction_events" IS 'Le journal d''apprentissage : gestes et contexte (ADR-0019).';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "offers_interaction_events";"""
