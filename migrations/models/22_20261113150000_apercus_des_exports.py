from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Les aperçus des exports : chaque page d'un CV ou d'une lettre exportés,
# rendue en image à l'export, pour que l'application montre le document avant
# d'ouvrir le PDF. Les exports existants n'en ont pas (liste vide).
# Idempotente ; à appliquer avant le code.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_generated_cvs" ADD COLUMN IF NOT EXISTS "preview_keys" JSONB NOT NULL DEFAULT '[]'::jsonb;
COMMENT ON COLUMN "documents_generated_cvs"."preview_keys" IS 'Les pages rendues en image, dans l''ordre.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_generated_cvs" DROP COLUMN IF EXISTS "preview_keys";"""
