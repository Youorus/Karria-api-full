from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "offers_conversations" ADD COLUMN IF NOT EXISTS "pending_widenings" JSONB;
COMMENT ON COLUMN "offers_conversations"."pending_widenings" IS 'Les élargissements proposés au dernier tour de recherche.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "offers_conversations" DROP COLUMN IF EXISTS "pending_widenings";"""
