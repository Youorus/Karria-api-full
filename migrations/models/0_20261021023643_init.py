from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "identity_login_challenges" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "email" VARCHAR(254) NOT NULL,
    "user_id" UUID,
    "code_hash" VARCHAR(64) NOT NULL,
    "status" VARCHAR(16) NOT NULL,
    "expires_at" TIMESTAMPTZ NOT NULL,
    "code_sent_at" TIMESTAMPTZ NOT NULL,
    "code_sends" INT NOT NULL,
    "attempts" INT NOT NULL,
    "completed_at" TIMESTAMPTZ,
    "created_account" BOOL NOT NULL,
    "terms_version" VARCHAR(32) NOT NULL,
    "privacy_policy_version" VARCHAR(32) NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_identity_lo_status_3f849f" ON "identity_login_challenges" ("status");
CREATE INDEX IF NOT EXISTS "idx_identity_lo_expires_513732" ON "identity_login_challenges" ("expires_at");
COMMENT ON COLUMN "identity_login_challenges"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "identity_login_challenges"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "identity_login_challenges"."email" IS 'Adresse visée, normalisée.';
COMMENT ON COLUMN "identity_login_challenges"."user_id" IS 'Pas de clé étrangère : le compte peut ne pas exister encore, et le';
COMMENT ON COLUMN "identity_login_challenges"."code_hash" IS 'Condensat du code en cours.';
COMMENT ON COLUMN "identity_login_challenges"."status" IS 'OPEN: open\nCOMPLETED: completed\nFAILED: failed';
COMMENT ON TABLE "identity_login_challenges" IS 'Parcours de connexion par code e-mail, codes hachés.';
CREATE TABLE IF NOT EXISTS "identity_users" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "email" VARCHAR(254) NOT NULL UNIQUE,
    "email_verified_at" TIMESTAMPTZ NOT NULL,
    "terms_version" VARCHAR(32) NOT NULL,
    "privacy_policy_version" VARCHAR(32) NOT NULL,
    "consent_accepted_at" TIMESTAMPTZ NOT NULL,
    "last_login_at" TIMESTAMPTZ
);
COMMENT ON COLUMN "identity_users"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "identity_users"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "identity_users"."email" IS 'Adresse normalisée (minuscules). Unique : une boîte, un compte.';
COMMENT ON COLUMN "identity_users"."email_verified_at" IS 'Quand l''adresse a été prouvée.';
COMMENT ON COLUMN "identity_users"."consent_accepted_at" IS 'Horodatage du consentement donné « en continuant ».';
COMMENT ON TABLE "identity_users" IS 'Comptes Karria : une adresse e-mail prouvée, rien d''autre.';
CREATE TABLE IF NOT EXISTS "identity_provider_identities" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "provider" VARCHAR(16) NOT NULL,
    "subject" VARCHAR(255) NOT NULL,
    "email" VARCHAR(254),
    "last_used_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL REFERENCES "identity_users" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_identity_pr_provide_11dd49" UNIQUE ("provider", "subject")
);
CREATE INDEX IF NOT EXISTS "idx_identity_pr_user_id_f877c3" ON "identity_provider_identities" ("user_id");
COMMENT ON COLUMN "identity_provider_identities"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "identity_provider_identities"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "identity_provider_identities"."provider" IS 'APPLE: apple\nGOOGLE: google';
COMMENT ON COLUMN "identity_provider_identities"."subject" IS 'Le `sub` signé par le fournisseur. C''est lui la clé.';
COMMENT ON COLUMN "identity_provider_identities"."email" IS 'Indicative : l''adresse transmise au rattachement. N''identifie jamais.';
COMMENT ON TABLE "identity_provider_identities" IS 'Rattachements Apple / Google, clés sur le `sub`.';
CREATE TABLE IF NOT EXISTS "identity_sessions" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "access_token_hash" VARCHAR(64) NOT NULL UNIQUE,
    "refresh_token_hash" VARCHAR(64) NOT NULL UNIQUE,
    "previous_refresh_token_hash" VARCHAR(64),
    "access_expires_at" TIMESTAMPTZ NOT NULL,
    "refresh_expires_at" TIMESTAMPTZ NOT NULL,
    "rotation_count" INT NOT NULL,
    "user_agent" VARCHAR(255),
    "last_used_at" TIMESTAMPTZ,
    "ended_at" TIMESTAMPTZ,
    "end_reason" VARCHAR(32),
    "user_id" UUID NOT NULL REFERENCES "identity_users" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_identity_se_previou_73ef85" ON "identity_sessions" ("previous_refresh_token_hash");
CREATE INDEX IF NOT EXISTS "idx_identity_se_refresh_52a809" ON "identity_sessions" ("refresh_expires_at");
CREATE INDEX IF NOT EXISTS "idx_identity_se_ended_a_46d625" ON "identity_sessions" ("ended_at");
COMMENT ON COLUMN "identity_sessions"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "identity_sessions"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "identity_sessions"."access_token_hash" IS 'Condensat SHA-256 du jeton d''accès. Jamais le jeton.';
COMMENT ON COLUMN "identity_sessions"."refresh_token_hash" IS 'Condensat SHA-256 du jeton de rafraîchissement courant.';
COMMENT ON COLUMN "identity_sessions"."previous_refresh_token_hash" IS 'Condensat de la génération précédente. C''est lui qui rend la détection de vol possible : sans trace de l''ancien jeton, un rejeu ressemblerait à un jeton inconnu.';
COMMENT ON COLUMN "identity_sessions"."end_reason" IS 'LOGGED_OUT: logged_out\nLOGGED_OUT_EVERYWHERE: logged_out_everywhere\nTOKEN_REUSE_DETECTED: token_reuse_detected';
COMMENT ON TABLE "identity_sessions" IS 'Sessions d''authentification, jetons hachés.';
CREATE TABLE IF NOT EXISTS "devices_installations" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "installation_id" VARCHAR(128) NOT NULL,
    "platform" VARCHAR(16) NOT NULL,
    "push_token" VARCHAR(256),
    "push_permission" VARCHAR(16) NOT NULL,
    "os_name" VARCHAR(100) NOT NULL,
    "os_version" VARCHAR(100) NOT NULL,
    "device_brand" VARCHAR(100),
    "device_manufacturer" VARCHAR(100),
    "device_model" VARCHAR(100),
    "device_model_id" VARCHAR(100),
    "device_year_class" INT,
    "is_physical_device" BOOL NOT NULL,
    "app_version" VARCHAR(100) NOT NULL,
    "app_build" VARCHAR(100),
    "locale" VARCHAR(100),
    "timezone" VARCHAR(100),
    "region" VARCHAR(100),
    "country" VARCHAR(2),
    "first_seen_at" TIMESTAMPTZ NOT NULL,
    "last_seen_at" TIMESTAMPTZ NOT NULL,
    CONSTRAINT "uid_devices_ins_user_id_b716a6" UNIQUE ("user_id", "installation_id")
);
CREATE INDEX IF NOT EXISTS "idx_devices_ins_user_id_adf068" ON "devices_installations" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_devices_ins_push_to_b07762" ON "devices_installations" ("push_token");
CREATE INDEX IF NOT EXISTS "idx_devices_ins_last_se_c8fc5b" ON "devices_installations" ("last_seen_at");
COMMENT ON COLUMN "devices_installations"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "devices_installations"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "devices_installations"."user_id" IS 'Compte connecté lors de la déclaration. Pas de clé étrangère : `devices` ne lit pas la table d''`identity` (règle 4).';
COMMENT ON COLUMN "devices_installations"."platform" IS 'IOS: ios\nANDROID: android';
COMMENT ON COLUMN "devices_installations"."push_token" IS 'Jeton Expo. Nul tant que l''app n''en a pas, ou dès qu''Expo l''a déclaré mort.';
COMMENT ON COLUMN "devices_installations"."push_permission" IS 'GRANTED: granted\nDENIED: denied\nUNDETERMINED: undetermined';
COMMENT ON COLUMN "devices_installations"."country" IS 'ISO 3166-1, d''après `CF-IPCountry`. Jamais une IP.';
COMMENT ON TABLE "devices_installations" IS 'Installations de l''app par compte, et leur jeton push.';
CREATE TABLE IF NOT EXISTS "devices_push_tickets" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "ticket_id" VARCHAR(64) NOT NULL UNIQUE,
    "push_token" VARCHAR(256) NOT NULL,
    "checked_at" TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS "idx_devices_pus_checked_c48c43" ON "devices_push_tickets" ("checked_at");
COMMENT ON COLUMN "devices_push_tickets"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "devices_push_tickets"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "devices_push_tickets" IS 'Tickets Expo en attente de reçu, pour détacher les jetons morts.';
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
    "xDc/7ODkqV9i/u3/30DLnQ=="
)
