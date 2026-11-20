from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Les recherches enregistrées (ADR-0041) : chaque recherche qu'une personne
# applique est retenue, avec son alerte, pour qu'elle la retrouve dans ses
# paramètres et l'active ou la coupe. Table neuve ; la recherche en cours de
# chaque personne (`offers_criteria`) y est reportée une fois, sans rien
# modifier ni effacer (le rythme des alertes reste compté sur `offers_criteria`).
# Idempotente ; à appliquer **avant** le code (sans la
# table, appliquer une recherche répond 500).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "offers_saved_searches" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "criteria" JSONB NOT NULL,
    "fingerprint" VARCHAR(32) NOT NULL,
    "alerts_enabled" BOOL NOT NULL DEFAULT True,
    "last_used_at" TIMESTAMPTZ NOT NULL,
    "target_keys" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "last_alert_at" TIMESTAMPTZ,
    "alerts_sent_on" TIMESTAMPTZ,
    "alerts_sent_today" INT NOT NULL DEFAULT 0,
    "notified_offer_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT "uid_offers_save_user_id_fp" UNIQUE ("user_id", "fingerprint")
);
CREATE INDEX IF NOT EXISTS "idx_offers_save_user_id" ON "offers_saved_searches" ("user_id");
COMMENT ON COLUMN "offers_saved_searches"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_saved_searches"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "offers_saved_searches"."criteria" IS '`SearchCriteria` sérialisés.';
COMMENT ON COLUMN "offers_saved_searches"."last_used_at" IS 'Dernière fois où elle a été appliquée.';
COMMENT ON COLUMN "offers_saved_searches"."target_keys" IS 'Cibles de collecte demandées.';
COMMENT ON COLUMN "offers_saved_searches"."notified_offer_ids" IS 'Dernières offres déjà signalées.';
COMMENT ON TABLE "offers_saved_searches" IS 'Les recherches que chaque personne a faites, et leurs alertes.';
        INSERT INTO "offers_saved_searches" ("id", "created_at", "updated_at", "user_id", "criteria", "fingerprint", "alerts_enabled", "last_used_at", "last_alert_at", "alerts_sent_on", "alerts_sent_today")
        SELECT gen_random_uuid(), c."created_at", c."updated_at", c."user_id", c."criteria", c."fingerprint", c."alerts_enabled", c."updated_at", NULL, NULL, 0
        FROM "offers_criteria" c
        ON CONFLICT ("user_id", "fingerprint") DO NOTHING;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "offers_saved_searches";"""
