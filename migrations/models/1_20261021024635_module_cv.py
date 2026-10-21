from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "cv_curricula" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL UNIQUE,
    "identity" JSONB,
    "contact" JSONB,
    "summary" TEXT,
    "image_key" VARCHAR(512),
    "experiences" JSONB NOT NULL,
    "educations" JSONB NOT NULL,
    "skills" JSONB NOT NULL,
    "languages" JSONB NOT NULL,
    "interests" JSONB NOT NULL,
    "projects" JSONB NOT NULL
);
COMMENT ON COLUMN "cv_curricula"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "cv_curricula"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "cv_curricula"."user_id" IS 'Compte propriétaire.';
COMMENT ON COLUMN "cv_curricula"."image_key" IS 'Clé objet de la photo (bucket media).';
COMMENT ON TABLE "cv_curricula" IS 'Le CV d''une personne, sections comprises. Un seul par `user_id`.';
        CREATE TABLE IF NOT EXISTS "cv_drafts" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL UNIQUE,
    "chapters" JSONB NOT NULL
);
COMMENT ON COLUMN "cv_drafts"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "cv_drafts"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "cv_drafts"."user_id" IS 'Compte propriétaire.';
COMMENT ON COLUMN "cv_drafts"."chapters" IS 'État des chapitres : {clé: {status, data, items}}.';
COMMENT ON TABLE "cv_drafts" IS 'Le brouillon en cours d''une personne. Un seul par `user_id`.';
        CREATE TABLE IF NOT EXISTS "cv_photos" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "image_key" VARCHAR(512) NOT NULL,
    "enhanced" BOOL NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_cv_photos_user_id_32c3f9" ON "cv_photos" ("user_id");
COMMENT ON COLUMN "cv_photos"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "cv_photos"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "cv_photos"."user_id" IS 'Compte propriétaire.';
COMMENT ON TABLE "cv_photos" IS 'Une photo envoyée pour le CV, en attente d''être retenue ou oubliée.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "cv_drafts";
        DROP TABLE IF EXISTS "cv_curricula";
        DROP TABLE IF EXISTS "cv_photos";"""


MODELS_STATE = (
    "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"
)
