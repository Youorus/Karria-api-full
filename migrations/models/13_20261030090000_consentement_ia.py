from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `identity` (ADR-0025) : le consentement à l'IA d'une personne — la
# version de l'information acceptée, les fournisseurs et usages nommés, la
# date, et le retrait s'il a eu lieu. Une ligne par compte, emportée avec lui
# (clé étrangère en cascade). Idempotente, à appliquer **avant** le code qui
# la lit : sans elle, `GET /auth/me` répond 500 et toute route d'IA refuse.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "identity_ai_consents" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "version" VARCHAR(64) NOT NULL,
    "providers" JSONB NOT NULL,
    "purposes" JSONB NOT NULL,
    "accepted_at" TIMESTAMPTZ NOT NULL,
    "revoked_at" TIMESTAMPTZ,
    "user_id" UUID NOT NULL UNIQUE REFERENCES "identity_users" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "identity_ai_consents"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "identity_ai_consents"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "identity_ai_consents"."version" IS 'La version de l''information acceptée (`2026-10.<empreinte>`).';
COMMENT ON COLUMN "identity_ai_consents"."providers" IS 'Les fournisseurs nommés à la personne à ce moment-là.';
COMMENT ON COLUMN "identity_ai_consents"."purposes" IS 'Les usages nommés à la personne à ce moment-là.';
COMMENT ON COLUMN "identity_ai_consents"."accepted_at" IS 'Quand la personne a accepté.';
COMMENT ON COLUMN "identity_ai_consents"."revoked_at" IS 'Quand elle s''est ravisée, le cas échéant.';
COMMENT ON TABLE "identity_ai_consents" IS 'Consentement à l''IA : version acceptée, fournisseurs, retrait.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "identity_ai_consents";"""
