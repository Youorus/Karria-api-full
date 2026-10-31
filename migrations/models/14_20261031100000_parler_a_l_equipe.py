from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `support` (ADR-0026) : « Parler à l'équipe ». Une conversation par
# compte (`user_id` unique, sans clé étrangère : le module ne lit pas
# `identity`), et ses messages — un texte, ou la **clé** d'un vocal ou d'une
# image dans le bucket `support`. Idempotente, à appliquer **avant** le code :
# sans les tables, `GET /support/me` répond 500.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "support_conversations" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL UNIQUE,
    "last_user_message_at" TIMESTAMPTZ,
    "last_team_message_at" TIMESTAMPTZ,
    "user_read_at" TIMESTAMPTZ,
    "team_read_at" TIMESTAMPTZ,
    "team_alerted_at" TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS "idx_support_con_last_us_0f3c1a" ON "support_conversations" ("last_user_message_at");
COMMENT ON COLUMN "support_conversations"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "support_conversations"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "support_conversations"."user_id" IS 'Le compte. Pas de clé étrangère : `support` ne lit pas `identity` (règle 4).';
COMMENT ON COLUMN "support_conversations"."team_alerted_at" IS 'Dernier courriel envoyé à l''équipe pour ce fil.';
COMMENT ON TABLE "support_conversations" IS '« Parler à l''équipe » : un fil par compte, et ses lectures.';
        CREATE TABLE IF NOT EXISTS "support_messages" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "author" VARCHAR(8) NOT NULL,
    "kind" VARCHAR(8) NOT NULL,
    "body" TEXT,
    "attachment_key" VARCHAR(512),
    "attachment_content_type" VARCHAR(64),
    "attachment_size_bytes" INT,
    "attachment_duration_ms" INT,
    "conversation_id" UUID NOT NULL REFERENCES "support_conversations" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_support_mes_convers_7b2e41" ON "support_messages" ("conversation_id", "created_at");
COMMENT ON COLUMN "support_messages"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "support_messages"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "support_messages"."author" IS 'USER: user\nTEAM: team';
COMMENT ON COLUMN "support_messages"."kind" IS 'TEXT: text\nVOICE: voice\nIMAGE: image';
COMMENT ON COLUMN "support_messages"."body" IS 'Le texte, au plus 4000 caractères.';
COMMENT ON COLUMN "support_messages"."attachment_key" IS 'Clé dans le bucket `support`.';
COMMENT ON COLUMN "support_messages"."attachment_duration_ms" IS 'Durée d''un vocal.';
COMMENT ON TABLE "support_messages" IS 'Les messages des fils « Parler à l''équipe ».';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "support_messages";
        DROP TABLE IF EXISTS "support_conversations";"""
