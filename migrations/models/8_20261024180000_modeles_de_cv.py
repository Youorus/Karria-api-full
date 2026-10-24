from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `documents` (ADR-0020) : modèles de CV et leurs versions, possession,
# choix, exports. Aucune clé étrangère vers les comptes (ADR-0015) ; des clés
# d'objets, jamais d'URL (ADR-0008).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "documents_generated_cvs" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "template_id" UUID NOT NULL,
    "template_version_id" UUID NOT NULL,
    "template_label" VARCHAR(120) NOT NULL,
    "template_version" INT NOT NULL,
    "ats_safe" BOOL NOT NULL,
    "offer_id" UUID,
    "pdf_key" VARCHAR(512),
    "docx_key" VARCHAR(512),
    "fit" JSONB NOT NULL,
    "ats" JSONB NOT NULL,
    "ats_docx" JSONB,
    "explanation" JSONB,
    "explanation_status" VARCHAR(16) NOT NULL,
    "full_name" VARCHAR(210) NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_documents_g_user_id_d041e2" ON "documents_generated_cvs" ("user_id");
COMMENT ON COLUMN "documents_generated_cvs"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "documents_generated_cvs"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "documents_generated_cvs" IS 'Un CV exporté.';
CREATE TABLE IF NOT EXISTS "documents_selections" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL UNIQUE,
    "template_id" UUID NOT NULL,
    "ats_safe" BOOL NOT NULL
);
COMMENT ON COLUMN "documents_selections"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "documents_selections"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "documents_selections" IS 'Le modèle choisi par une personne.';
CREATE TABLE IF NOT EXISTS "documents_templates" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "name" VARCHAR(80) NOT NULL UNIQUE,
    "label" VARCHAR(120) NOT NULL,
    "category" VARCHAR(40) NOT NULL,
    "description" TEXT NOT NULL,
    "tags" JSONB NOT NULL,
    "is_premium" BOOL NOT NULL,
    "price" DECIMAL(8,2),
    "primary_color" VARCHAR(9),
    "font_family" VARCHAR(80),
    "has_photo" BOOL NOT NULL,
    "columns" SMALLINT NOT NULL,
    "published_version_id" UUID,
    "published_version" INT,
    "views_count" INT NOT NULL,
    "uses_count" INT NOT NULL,
    "rating_avg" DOUBLE PRECISION NOT NULL,
    "rating_count" INT NOT NULL
);
COMMENT ON COLUMN "documents_templates"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "documents_templates"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "documents_templates"."name" IS 'Nom technique (slug).';
COMMENT ON COLUMN "documents_templates"."published_version_id" IS 'Version publiée.';
COMMENT ON TABLE "documents_templates" IS 'Un modèle de CV (galerie).';
CREATE TABLE IF NOT EXISTS "documents_template_versions" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "template_id" UUID NOT NULL,
    "version" INT NOT NULL,
    "status" VARCHAR(16) NOT NULL,
    "source_key" VARCHAR(512) NOT NULL,
    "spec" JSONB,
    "sample" JSONB,
    "labels" JSONB,
    "report" JSONB NOT NULL,
    "assets" JSONB NOT NULL,
    "fonts" JSONB NOT NULL,
    "thumbnail_key" VARCHAR(512),
    "thumbnail_blurhash" VARCHAR(64),
    "preview_key" VARCHAR(512),
    "full_key" VARCHAR(512),
    "error" TEXT,
    "published_at" TIMESTAMPTZ,
    CONSTRAINT "uid_documents_t_templat_66c03c" UNIQUE ("template_id", "version")
);
CREATE INDEX IF NOT EXISTS "idx_documents_t_templat_48efbb" ON "documents_template_versions" ("template_id");
CREATE INDEX IF NOT EXISTS "idx_documents_t_status_f878f3" ON "documents_template_versions" ("status");
COMMENT ON COLUMN "documents_template_versions"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "documents_template_versions"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "documents_template_versions" IS 'Une version analysée d''un modèle.';
CREATE TABLE IF NOT EXISTS "documents_user_templates" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "template_id" UUID NOT NULL,
    "source" VARCHAR(16) NOT NULL,
    CONSTRAINT "uid_documents_u_user_id_830eea" UNIQUE ("user_id", "template_id")
);
CREATE INDEX IF NOT EXISTS "idx_documents_u_user_id_1a3413" ON "documents_user_templates" ("user_id");
COMMENT ON COLUMN "documents_user_templates"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "documents_user_templates"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON TABLE "documents_user_templates" IS 'Un modèle possédé par une personne.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "documents_generated_cvs";
        DROP TABLE IF EXISTS "documents_selections";
        DROP TABLE IF EXISTS "documents_templates";
        DROP TABLE IF EXISTS "documents_template_versions";
        DROP TABLE IF EXISTS "documents_user_templates";"""
