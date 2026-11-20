from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# D'où viennent les inscrits, sans le leur demander (demande de Marc,
# 2026-11-20, ADR-0042) : deux colonnes facultatives sur le compte — la région
# du téléphone (code ISO 3166-1 alpha-2 du réglage « Région », annoncé par
# l'app, jamais une adresse IP) et son fuseau horaire. Écrites une seule fois,
# à la création du compte ou à la première occasion ; elles partent avec le
# compte. Les comptes existants restent à NULL : ils se rempliront à leur
# prochaine connexion ou déclaration d'appareil. Idempotente ; à appliquer
# **avant** le code (sans les colonnes, toute lecture d'un compte répond 500).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "identity_users" ADD COLUMN IF NOT EXISTS "signup_region" VARCHAR(2);
ALTER TABLE "identity_users" ADD COLUMN IF NOT EXISTS "signup_timezone" VARCHAR(64);
COMMENT ON COLUMN "identity_users"."signup_region" IS 'Région du téléphone à l''inscription (ISO 3166-1 alpha-2), ou NULL.';
COMMENT ON COLUMN "identity_users"."signup_timezone" IS 'Fuseau horaire du téléphone à l''inscription (nom IANA), ou NULL.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "identity_users" DROP COLUMN IF EXISTS "signup_region";
ALTER TABLE "identity_users" DROP COLUMN IF EXISTS "signup_timezone";"""
