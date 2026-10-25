from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `offers` : les entreprises (`offers_companies`) et le lien de chaque
# offre à la sienne, avec la copie de son logo (le fil se lit sans jointure).
# `documents_template_versions.thumbnail_blurhash` manquait à l'instantané de
# la migration 8, mais la colonne existe déjà (créée par son SQL) : aerich
# l'ajoutait ici, retiré à la main.
# Idempotente (`IF NOT EXISTS`) : appliquée en production avant le déploiement
# du code qui la lit, puis enregistrée par `aerich upgrade`.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "offers_companies" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "key" VARCHAR(255) NOT NULL,
    "domain" VARCHAR(255) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "website" VARCHAR(2048),
    "source_logo_url" VARCHAR(2048),
    "v1_id" BIGINT UNIQUE,
    "v1_logo_url" VARCHAR(2048),
    "logo_hash" VARCHAR(64),
    "logo_origin" VARCHAR(16),
    "logo_status" VARCHAR(16) NOT NULL,
    "logo_attempts" SMALLINT NOT NULL,
    "logo_next_attempt_at" TIMESTAMPTZ,
    "logo_checked_at" TIMESTAMPTZ,
    "logo_rejected_urls" JSONB NOT NULL,
    CONSTRAINT "uid_offers_comp_key_1ec891" UNIQUE ("key", "domain")
);
CREATE INDEX IF NOT EXISTS "idx_offers_comp_logo_ha_d750b3" ON "offers_companies" ("logo_hash");
CREATE INDEX IF NOT EXISTS "idx_offers_comp_logo_ne_e6f348" ON "offers_companies" ("logo_next_attempt_at");
CREATE INDEX IF NOT EXISTS "idx_offers_comp_logo_st_61690e" ON "offers_companies" ("logo_status", "logo_next_attempt_at");
COMMENT ON COLUMN "offers_companies"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_companies"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "offers_companies"."key" IS 'Clé de nom (pliée, sans forme juridique).';
COMMENT ON COLUMN "offers_companies"."domain" IS 'Domaine du site ; vide si inconnu.';
COMMENT ON COLUMN "offers_companies"."v1_id" IS '`companies.id` de la V1.';
COMMENT ON COLUMN "offers_companies"."logo_origin" IS 'SOURCE: source\nV1: v1\nFAVICON: favicon';
COMMENT ON COLUMN "offers_companies"."logo_status" IS 'PENDING: pending\nREADY: ready\nMISSING: missing';
COMMENT ON TABLE "offers_companies" IS 'Employeurs reconnus par clé de nom et domaine ; leur logo normalisé vit dans le bucket public `logos`, adressé par `logo_hash`.';
        ALTER TABLE "offers_offers" ADD COLUMN IF NOT EXISTS "company_id" UUID;
        ALTER TABLE "offers_offers" ADD COLUMN IF NOT EXISTS "company_logo_hash" VARCHAR(64);
        COMMENT ON COLUMN offers_offers."company_id" IS 'L''entreprise rattachée (`offers_companies`).';
COMMENT ON COLUMN offers_offers."company_logo_hash" IS 'Copie du logo de l''entreprise : le fil se lit sans jointure.';
        CREATE INDEX IF NOT EXISTS "idx_offers_offe_company_f003bd" ON "offers_offers" ("company_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_offers_offe_company_f003bd";
        ALTER TABLE "offers_offers" DROP COLUMN "company_id";
        ALTER TABLE "offers_offers" DROP COLUMN "company_logo_hash";
        DROP TABLE IF EXISTS "offers_companies";"""


MODELS_STATE = (
    "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"
)
