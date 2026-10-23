from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "offers_scrape_targets" ADD "location_key" VARCHAR(8) NOT NULL DEFAULT 'fr';
        ALTER TABLE "offers_scrape_targets" ADD "contract" VARCHAR(16);
        ALTER TABLE "offers_scrape_targets" ADD "state" VARCHAR(16) NOT NULL DEFAULT 'probation';
        ALTER TABLE "offers_scrape_targets" ADD "thin_since" TIMESTAMPTZ;
        ALTER TABLE "offers_scrape_targets" ADD "scrapes" INT NOT NULL DEFAULT 0;
        ALTER TABLE "offers_scrape_targets" ADD "yield_ema" DOUBLE PRECISION NOT NULL DEFAULT 0;
        ALTER TABLE "offers_scrape_targets" ADD "zero_streak" INT NOT NULL DEFAULT 0;
        ALTER TABLE "offers_scrape_targets" ADD "overlap_ema" DOUBLE PRECISION NOT NULL DEFAULT 0;
        ALTER TABLE "offers_scrape_targets" ADD "gate_rejects" INT NOT NULL DEFAULT 0;
        COMMENT ON COLUMN "offers_scrape_targets"."location_key" IS 'Département (« 75 », « 2A ») ou « fr ».';
        COMMENT ON COLUMN "offers_scrape_targets"."weight" IS 'Personnes derrière la cible.';
        COMMENT ON COLUMN "offers_scrape_targets"."thin_since" IS 'Dernière recherche maigre qui l''a demandée.';
        COMMENT ON COLUMN "offers_scrape_targets"."yield_ema" IS 'Offres nouvelles par passage, lissées.';
        COMMENT ON COLUMN "offers_scrape_targets"."overlap_ema" IS 'Part des annonces déjà rapportées par France Travail, lissée.';
        COMMENT ON TABLE "offers_scrape_targets" IS 'Ce que la collecte demande aux sites d''offres, quand, et avec quel rendement.';
        -- Les cibles déjà collectées ne repassent pas par l'essai.
        UPDATE "offers_scrape_targets" SET "state" = 'active' WHERE "last_scraped_at" IS NOT NULL;
        CREATE TABLE IF NOT EXISTS "offers_target_demands" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "target_key" VARCHAR(512) NOT NULL,
    "demanded_at" TIMESTAMPTZ NOT NULL,
    CONSTRAINT "uid_offers_targ_user_id_4f2c1a" UNIQUE ("user_id", "target_key")
);
CREATE INDEX IF NOT EXISTS "idx_offers_targ_target__a1b2c3" ON "offers_target_demands" ("target_key");
COMMENT ON COLUMN "offers_target_demands"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_target_demands"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "offers_target_demands"."demanded_at" IS 'Dernière recherche de la personne sur cette cible.';
COMMENT ON TABLE "offers_target_demands" IS 'Qui demande quelle cible de collecte, depuis quand.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "offers_target_demands";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "gate_rejects";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "overlap_ema";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "zero_streak";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "yield_ema";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "scrapes";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "thin_since";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "state";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "contract";
        ALTER TABLE "offers_scrape_targets" DROP COLUMN "location_key";"""
