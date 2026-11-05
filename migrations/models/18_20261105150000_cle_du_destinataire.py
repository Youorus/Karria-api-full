from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `documents` (ADR-0031, revue de sécurité du 2026-11-05) : le quota par
# destinataire se compte sur une **clé canonique** (minuscules, étiquette `+`
# retirée), indexée, et non sur l'adresse telle que saisie — `RH@Acme.fr` et
# `rh+x@acme.fr` sont la même boîte. Les lignes existantes reçoivent leur clé.
# Idempotente, à appliquer **avant** le code (sans la colonne, tout envoi répond 500).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_sends" ADD COLUMN IF NOT EXISTS "to_key" VARCHAR(254) NOT NULL DEFAULT '';
UPDATE "documents_sends" SET "to_key" = lower(split_part(split_part("to_address", '@', 1), '+', 1)) || '@' || lower(split_part("to_address", '@', 2)) WHERE "to_key" = '';
CREATE INDEX IF NOT EXISTS "idx_documents_s_to_key_7c3e21" ON "documents_sends" ("to_key");
COMMENT ON COLUMN "documents_sends"."to_key" IS 'Clé canonique du destinataire (minuscules, sans étiquette +) : le quota se compte dessus.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_sends" DROP COLUMN IF EXISTS "to_key";"""
