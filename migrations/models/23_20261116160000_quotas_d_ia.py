from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Les quotas d'IA par personne (ADR-0038) : deux registres, une ligne par
# action d'IA rendue, sur lesquels se comptent les plafonds mensuels.
# - `cv_ai_usages` (module `cv`) : portraits et imports de CV ;
# - `applications_ai_usages` (module `applications`) : dossiers, lettres et
#   paragraphes réécrits.
# Tables neuves, rien n'est modifié ni effacé. Idempotente, à appliquer
# **avant** le code : sans les tables, toute action soumise à quota répond 500.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "cv_ai_usages" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "kind" VARCHAR(16) NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_cv_ai_usage_user_id_3e7a51" ON "cv_ai_usages" ("user_id");
COMMENT ON COLUMN "cv_ai_usages"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "cv_ai_usages"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "cv_ai_usages"."user_id" IS 'Compte bénéficiaire.';
COMMENT ON COLUMN "cv_ai_usages"."kind" IS 'portrait ou import.';
COMMENT ON TABLE "cv_ai_usages" IS 'Une action d''IA rendue (portrait, import) — le registre des quotas mensuels.';
CREATE TABLE IF NOT EXISTS "applications_ai_usages" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "kind" VARCHAR(16) NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_application_user_id_9c4d27" ON "applications_ai_usages" ("user_id");
COMMENT ON COLUMN "applications_ai_usages"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "applications_ai_usages"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "applications_ai_usages"."user_id" IS 'Compte bénéficiaire.';
COMMENT ON COLUMN "applications_ai_usages"."kind" IS 'dossier, letter ou paragraph.';
COMMENT ON TABLE "applications_ai_usages" IS 'Une action d''IA rendue (dossier, lettre, paragraphe) — le registre des quotas mensuels.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "applications_ai_usages";
DROP TABLE IF EXISTS "cv_ai_usages";"""
