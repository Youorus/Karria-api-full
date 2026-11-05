from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `identity` (ADR-0025, section « 2026-11-05 — accord à l'inscription ») :
# l'écran de connexion dit désormais aussi « et que Karria s'appuie sur des
# modèles d'IA » ; la version de l'information affichée voyage avec
# `POST /auth/email/start` et doit survivre jusqu'à la validation du code,
# comme `terms_version`. Colonne NULL : un client qui ne l'envoie pas n'est
# pas cassé. Idempotente, à appliquer **avant** le code (sans la colonne,
# l'ouverture d'un parcours de connexion répond 500).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "identity_login_challenges" ADD COLUMN IF NOT EXISTS "ai_disclosure_version" VARCHAR(64);
COMMENT ON COLUMN "identity_login_challenges"."ai_disclosure_version" IS 'Version de l''information sur l''IA acceptée « en continuant », ou NULL.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "identity_login_challenges" DROP COLUMN IF EXISTS "ai_disclosure_version";"""
