from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "offers_karria_memory" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL UNIQUE,
    "facts" JSONB NOT NULL,
    "constitution_version" VARCHAR(16) NOT NULL DEFAULT ''
);
COMMENT ON COLUMN "offers_karria_memory"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "offers_karria_memory"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "offers_karria_memory"."facts" IS '`MemoryFacts` sérialisés.';
COMMENT ON TABLE "offers_karria_memory" IS 'La mémoire de Karria sur chaque personne (visible et effaçable par elle).';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "offers_karria_memory";"""
