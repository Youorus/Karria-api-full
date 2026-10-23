from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "offers_conversations" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "last_message_at" TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_offers_conv_user_id_ef26fa" ON "offers_conversations" ("user_id");
COMMENT ON COLUMN "offers_conversations"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_conversations"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "offers_conversations" IS 'Conversations de recherche avec Karria.';
        CREATE TABLE IF NOT EXISTS "offers_criteria" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL UNIQUE,
    "criteria" JSONB NOT NULL,
    "fingerprint" VARCHAR(32) NOT NULL,
    "alerts_enabled" BOOL NOT NULL,
    "last_alert_at" TIMESTAMPTZ,
    "alerts_sent_on" TIMESTAMPTZ,
    "alerts_sent_today" INT NOT NULL
);
COMMENT ON COLUMN "offers_criteria"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_criteria"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "offers_criteria"."criteria" IS '`SearchCriteria` sérialisés.';
COMMENT ON TABLE "offers_criteria" IS 'Ce que chaque personne cherche, et l''état de ses alertes.';
        CREATE TABLE IF NOT EXISTS "offers_feed_entries" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "offer_id" UUID NOT NULL,
    "score" DOUBLE PRECISION NOT NULL,
    "quality" VARCHAR(8) NOT NULL,
    "reasons" JSONB NOT NULL,
    "origin" VARCHAR(8) NOT NULL,
    "notified_at" TIMESTAMPTZ,
    CONSTRAINT "uid_offers_feed_user_id_9f0f70" UNIQUE ("user_id", "offer_id")
);
CREATE INDEX IF NOT EXISTS "idx_offers_feed_user_id_13038c" ON "offers_feed_entries" ("user_id", "score");
COMMENT ON COLUMN "offers_feed_entries"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_feed_entries"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "offers_feed_entries"."quality" IS 'HIGH: high\nMEDIUM: medium\nLOW: low';
COMMENT ON COLUMN "offers_feed_entries"."origin" IS 'SEARCH: search\nALERT: alert';
COMMENT ON TABLE "offers_feed_entries" IS 'Le fil de chaque personne.';
        CREATE TABLE IF NOT EXISTS "offers_geocode_cache" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "query" VARCHAR(255) NOT NULL UNIQUE,
    "found" BOOL NOT NULL,
    "city" VARCHAR(255),
    "postal_code" VARCHAR(10),
    "department_code" VARCHAR(3),
    "latitude" DOUBLE PRECISION,
    "longitude" DOUBLE PRECISION
);
COMMENT ON COLUMN "offers_geocode_cache"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_geocode_cache"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "offers_geocode_cache" IS 'Cache du géocodeur (Base Adresse Nationale).';
        CREATE TABLE IF NOT EXISTS "offers_index_outbox" (
    "id" BIGSERIAL NOT NULL PRIMARY KEY,
    "offer_id" UUID NOT NULL,
    "enqueued_at" TIMESTAMPTZ NOT NULL
);
COMMENT ON TABLE "offers_index_outbox" IS 'Ce que l''index doit apprendre : écrit dans la transaction de l''offre.';
        CREATE TABLE IF NOT EXISTS "offers_ingestion_runs" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "run_id" UUID NOT NULL UNIQUE,
    "started_at" TIMESTAMPTZ NOT NULL,
    "finished_at" TIMESTAMPTZ NOT NULL,
    "targets" INT NOT NULL,
    "fetched" INT NOT NULL,
    "inserted" INT NOT NULL,
    "refreshed" INT NOT NULL,
    "duplicates" INT NOT NULL,
    "near_duplicates" INT NOT NULL,
    "updated" INT NOT NULL,
    "errors" INT NOT NULL,
    "landing_keys" JSONB NOT NULL
);
COMMENT ON COLUMN "offers_ingestion_runs"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_ingestion_runs"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "offers_ingestion_runs" IS 'Trace des passages de collecte.';
        CREATE TABLE IF NOT EXISTS "offers_messages" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "conversation_id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "role" VARCHAR(16) NOT NULL,
    "content" TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_offers_mess_convers_0d1e50" ON "offers_messages" ("conversation_id");
CREATE INDEX IF NOT EXISTS "idx_offers_mess_user_id_3c3338" ON "offers_messages" ("user_id");
COMMENT ON COLUMN "offers_messages"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_messages"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "offers_messages" IS 'Messages échangés avec Karria.';
        CREATE TABLE IF NOT EXISTS "offers_offers" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "source" VARCHAR(16) NOT NULL,
    "source_id" VARCHAR(255) NOT NULL,
    "title" VARCHAR(255) NOT NULL,
    "company_name" VARCHAR(255),
    "company_key" VARCHAR(255),
    "company_logo_url" VARCHAR(2048),
    "company_url" VARCHAR(2048),
    "description" TEXT NOT NULL,
    "location_label" VARCHAR(255),
    "city" VARCHAR(255),
    "postal_code" VARCHAR(10),
    "department_code" VARCHAR(3),
    "latitude" DOUBLE PRECISION,
    "longitude" DOUBLE PRECISION,
    "contract_type" VARCHAR(16),
    "remote" VARCHAR(16),
    "schedule" VARCHAR(16),
    "experience" VARCHAR(16),
    "salary_min_monthly" INT,
    "salary_max_monthly" INT,
    "salary_raw" VARCHAR(255),
    "published_at" TIMESTAMPTZ NOT NULL,
    "first_seen_at" TIMESTAMPTZ NOT NULL,
    "last_seen_at" TIMESTAMPTZ NOT NULL,
    "apply_url" VARCHAR(2048),
    "origin_url" VARCHAR(2048),
    "is_active" BOOL NOT NULL,
    "deactivated_at" TIMESTAMPTZ,
    "content_hash" VARCHAR(64) NOT NULL,
    "simhash" BIGINT NOT NULL,
    "simhash_band0" SMALLINT NOT NULL,
    "simhash_band1" SMALLINT NOT NULL,
    "simhash_band2" SMALLINT NOT NULL,
    "simhash_band3" SMALLINT NOT NULL,
    "duplicate_of" UUID,
    "indexed_version" VARCHAR(128),
    "indexed_hash" VARCHAR(64),
    "indexed_at" TIMESTAMPTZ,
    CONSTRAINT "uid_offers_offe_source_137609" UNIQUE ("source", "source_id")
);
CREATE INDEX IF NOT EXISTS "idx_offers_offe_city_15f61d" ON "offers_offers" ("city");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_departm_fc5d51" ON "offers_offers" ("department_code");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_contrac_eae31b" ON "offers_offers" ("contract_type");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_content_f88f6d" ON "offers_offers" ("content_hash");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_duplica_9b0e48" ON "offers_offers" ("duplicate_of");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_is_acti_393413" ON "offers_offers" ("is_active", "published_at");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_company_336c80" ON "offers_offers" ("company_key", "simhash_band0");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_company_6ea5d2" ON "offers_offers" ("company_key", "simhash_band1");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_company_f2123b" ON "offers_offers" ("company_key", "simhash_band2");
CREATE INDEX IF NOT EXISTS "idx_offers_offe_company_dd6045" ON "offers_offers" ("company_key", "simhash_band3");
COMMENT ON COLUMN "offers_offers"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_offers"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "offers_offers"."source" IS 'INDEED: indeed\nHELLOWORK: hellowork\nLINKEDIN: linkedin\nGLASSDOOR: glassdoor\nGOOGLE: google\nMANUAL: manual';
COMMENT ON COLUMN "offers_offers"."company_key" IS 'Nom de l''employeur plié (minuscules, sans accents) : clé des quasi-doublons.';
COMMENT ON COLUMN "offers_offers"."contract_type" IS 'CDI: cdi\nCDD: cdd\nINTERIM: interim\nALTERNANCE: alternance\nSTAGE: stage\nFREELANCE: freelance\nSAISONNIER: saisonnier\nAUTRE: autre';
COMMENT ON COLUMN "offers_offers"."remote" IS 'ON_SITE: sur_site\nHYBRID: hybride\nREMOTE: teletravail';
COMMENT ON COLUMN "offers_offers"."schedule" IS 'FULL_TIME: temps_plein\nPART_TIME: temps_partiel';
COMMENT ON COLUMN "offers_offers"."experience" IS 'BEGINNER: debutant\nJUNIOR: junior\nCONFIRMED: confirme\nSENIOR: senior';
COMMENT ON COLUMN "offers_offers"."simhash" IS 'Empreinte SimHash 64 bits, stockée signée.';
COMMENT ON COLUMN "offers_offers"."duplicate_of" IS 'Offre canonique dont celle-ci est un doublon.';
COMMENT ON COLUMN "offers_offers"."indexed_hash" IS 'Condensat du contenu dont l''index porte le plongement.';
COMMENT ON TABLE "offers_offers" IS 'Offres d''emploi normalisées : la vérité, dont l''index est une projection. La ligne brute de la source vit dans le bucket `landing`, pas ici.';
        CREATE TABLE IF NOT EXISTS "offers_offer_states" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "offer_id" UUID NOT NULL,
    "seen" BOOL NOT NULL,
    "opened" BOOL NOT NULL,
    "link_clicked" BOOL NOT NULL,
    "saved" BOOL NOT NULL,
    "applied" BOOL NOT NULL,
    "dismissed" BOOL NOT NULL,
    "seen_at" TIMESTAMPTZ,
    "opened_at" TIMESTAMPTZ,
    "saved_at" TIMESTAMPTZ,
    "applied_at" TIMESTAMPTZ,
    "dismissed_at" TIMESTAMPTZ,
    CONSTRAINT "uid_offers_offe_user_id_34efbd" UNIQUE ("user_id", "offer_id")
);
COMMENT ON COLUMN "offers_offer_states"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_offer_states"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "offers_offer_states" IS 'Vue, ouverte, sauvegardée, candidatée, écartée : par personne et par offre.';
        CREATE TABLE IF NOT EXISTS "offers_scrape_targets" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "key" VARCHAR(512) NOT NULL UNIQUE,
    "search_term" VARCHAR(255) NOT NULL,
    "location" VARCHAR(255) NOT NULL,
    "origin" VARCHAR(16) NOT NULL,
    "weight" INT NOT NULL,
    "is_active" BOOL NOT NULL,
    "demanded_at" TIMESTAMPTZ NOT NULL,
    "distance_km" INT,
    "last_scraped_at" TIMESTAMPTZ,
    "next_scrape_at" TIMESTAMPTZ,
    "last_found" INT NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_offers_scra_next_sc_104204" ON "offers_scrape_targets" ("next_scrape_at");
COMMENT ON COLUMN "offers_scrape_targets"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_scrape_targets"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "offers_scrape_targets"."origin" IS 'SEED: seed\nDEMAND: demand';
COMMENT ON COLUMN "offers_scrape_targets"."demanded_at" IS 'Dernière fois que la cible a été (re)demandée.';
COMMENT ON TABLE "offers_scrape_targets" IS 'Ce que la collecte demande aux sites d''offres, et quand.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "offers_criteria";
        DROP TABLE IF EXISTS "offers_geocode_cache";
        DROP TABLE IF EXISTS "offers_conversations";
        DROP TABLE IF EXISTS "offers_offer_states";
        DROP TABLE IF EXISTS "offers_feed_entries";
        DROP TABLE IF EXISTS "offers_messages";
        DROP TABLE IF EXISTS "offers_scrape_targets";
        DROP TABLE IF EXISTS "offers_ingestion_runs";
        DROP TABLE IF EXISTS "offers_offers";
        DROP TABLE IF EXISTS "offers_index_outbox";"""


MODELS_STATE = (
    "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"
    "ZWs9ZSQK7OC7l5z/6/8BWoKNsw=="
)
