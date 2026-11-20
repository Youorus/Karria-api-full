from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# L'index du fil : les offres actives et canoniques, les plus récentes d'abord.
# La page du fil et son total lisaient l'index (is_active, published_at) puis
# filtraient les doublons dans la table (un tiers des lignes) ; le total
# parcourait la table entière (≈ 285 ms, tous les cœurs à 10 personnes
# simultanées, mesuré le 2026-11-20). L'index partiel ne contient que ce que le
# fil montre. Idempotente ; à appliquer avant ou après le code.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE INDEX IF NOT EXISTS "idx_offers_offe_feed" ON "offers_offers" ("published_at" DESC, "id" DESC) WHERE "is_active" AND "duplicate_of" IS NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_offers_offe_feed";"""
