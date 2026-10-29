from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `payments` (ADR-0024) : la projection du client et de l'abonnement
# « sans pub » (une ligne par compte), les paiements uniques confirmés (cafés,
# modèles) et le registre des notifications Stripe déjà traitées. Aucune clé
# étrangère vers les comptes (ADR-0015). Idempotente, appliquée **avant** le
# code qui la lit — sans clé Stripe, ce code ne lit rien.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "payments_accounts" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL UNIQUE,
    "customer_id" VARCHAR(255) NOT NULL UNIQUE,
    "status" VARCHAR(16) NOT NULL DEFAULT 'none',
    "subscription_id" VARCHAR(255),
    "current_period_ends_at" TIMESTAMPTZ,
    "cancel_at_period_end" BOOL NOT NULL DEFAULT False,
    "ends_at" TIMESTAMPTZ,
    "past_due_since" TIMESTAMPTZ,
    "synced_at" TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS "idx_payments_ac_subscri_7c1f2a" ON "payments_accounts" ("subscription_id");
COMMENT ON COLUMN "payments_accounts"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "payments_accounts"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "payments_accounts"."status" IS 'NONE: none\nACTIVE: active\nPAST_DUE: past_due\nCANCELED: canceled';
COMMENT ON TABLE "payments_accounts" IS 'Un client Stripe et l''abonnement « sans pub » d''une personne (une ligne par compte).';
CREATE TABLE IF NOT EXISTS "payments_purchases" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "kind" VARCHAR(16) NOT NULL,
    "session_id" VARCHAR(255) NOT NULL UNIQUE,
    "amount_cents" INT NOT NULL,
    "currency" VARCHAR(3) NOT NULL,
    "coffee_count" INT,
    "template_id" UUID,
    "paid_at" TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_payments_pu_user_id_3e9b61" ON "payments_purchases" ("user_id");
COMMENT ON COLUMN "payments_purchases"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "payments_purchases"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "payments_purchases"."kind" IS 'AD_FREE: ad_free\nCOFFEE: coffee\nTEMPLATE: template';
COMMENT ON TABLE "payments_purchases" IS 'Un paiement unique confirmé : un café, un modèle.';
CREATE TABLE IF NOT EXISTS "payments_webhook_events" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "event_id" VARCHAR(255) NOT NULL UNIQUE,
    "event_type" VARCHAR(80) NOT NULL
);
COMMENT ON COLUMN "payments_webhook_events"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "payments_webhook_events"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "payments_webhook_events" IS 'Une notification déjà traitée. Aucune donnée personnelle : la ligne survit au compte.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "payments_webhook_events";
        DROP TABLE IF EXISTS "payments_purchases";
        DROP TABLE IF EXISTS "payments_accounts";"""
