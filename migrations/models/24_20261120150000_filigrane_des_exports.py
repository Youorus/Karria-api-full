from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Le filigrane des exports (ADR-0040) : chaque CV ou lettre exporté retient
# s'il porte la ligne « réalisé avec Karria ». Les trois premiers CV du mois
# d'un compte gratuit n'en portent pas ; le compteur lit cette colonne. Les
# exports existants valent `false` (ils comptent comme sans filigrane).
# Idempotente ; à appliquer avant le code.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_generated_cvs" ADD COLUMN IF NOT EXISTS "watermarked" BOOL NOT NULL DEFAULT False;
COMMENT ON COLUMN "documents_generated_cvs"."watermarked" IS 'Le document porte le filigrane Karria (ADR-0040).';
        CREATE INDEX IF NOT EXISTS "idx_documents_g_clean_month" ON "documents_generated_cvs" ("user_id", "created_at") WHERE "kind" = 'cv' AND NOT "watermarked";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_documents_g_clean_month";
        ALTER TABLE "documents_generated_cvs" DROP COLUMN IF EXISTS "watermarked";"""
