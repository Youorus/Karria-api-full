from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Le registre des exports comptés (revue de sécurité du 2026-11-20) : les CV
# sans filigrane et les CV optimisés du mois se comptaient sur les exports
# eux-mêmes, que la personne peut supprimer — supprimer ses documents remettait
# ses compteurs à zéro. Ils sont désormais notés ici, hors de portée de la
# suppression d'un export. Table neuve, remplie une fois avec les exports encore
# présents de ce mois. Idempotente ; à appliquer **avant** le code (sans la
# table, un export répond 500).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "documents_export_usage" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "kind" VARCHAR(16) NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_documents_e_user_id_usage" ON "documents_export_usage" ("user_id");
COMMENT ON COLUMN "documents_export_usage"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "documents_export_usage"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "documents_export_usage"."kind" IS 'clean (sans filigrane) ou optimized.';
COMMENT ON TABLE "documents_export_usage" IS 'Exports comptés dans les limites du mois (ADR-0040).';
        INSERT INTO "documents_export_usage" ("id", "created_at", "updated_at", "user_id", "kind")
        SELECT gen_random_uuid(), g."created_at", g."created_at", g."user_id", 'clean'
        FROM "documents_generated_cvs" g
        WHERE g."kind" = 'cv' AND g."watermarked" = false
          AND g."created_at" >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
          AND NOT EXISTS (SELECT 1 FROM "documents_export_usage");
        INSERT INTO "documents_export_usage" ("id", "created_at", "updated_at", "user_id", "kind")
        SELECT gen_random_uuid(), g."created_at", g."created_at", g."user_id", 'optimized'
        FROM "documents_generated_cvs" g
        WHERE g."kind" = 'cv' AND g."ats_safe" = true
          AND g."created_at" >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
          AND NOT EXISTS (SELECT 1 FROM "documents_export_usage" WHERE "kind" = 'optimized');"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "documents_export_usage";"""
