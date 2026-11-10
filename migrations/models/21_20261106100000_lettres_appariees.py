from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# ADR-0036 : la lettre de motivation, modèle apparié ou lettre sobre.
# - `documents_template_versions` : la lettre appariée d'une version (PDF source,
#   spécification de lettre, contenu d'exemple, rôles des lignes, aperçus), NULL
#   quand le modèle n'en a pas ;
# - `documents_generated_cvs.letter_style` : le style servi d'un export de lettre
#   (`template` | `plain`), NULL pour un CV ;
# - `applications_dossiers.letter_style` : le style retenu sur le dossier
#   (`PUT /applications/{id}/letter-style`), NULL = par défaut.
# Idempotente ; à appliquer **avant** le code (sans les colonnes, la lecture d'une
# version de modèle répond 500).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_template_versions" ADD COLUMN IF NOT EXISTS "letter_source_key" VARCHAR(512);
        ALTER TABLE "documents_template_versions" ADD COLUMN IF NOT EXISTS "letter_spec" JSONB;
        ALTER TABLE "documents_template_versions" ADD COLUMN IF NOT EXISTS "letter_sample" JSONB;
        ALTER TABLE "documents_template_versions" ADD COLUMN IF NOT EXISTS "letter_labels" JSONB;
        ALTER TABLE "documents_template_versions" ADD COLUMN IF NOT EXISTS "letter_thumbnail_key" VARCHAR(512);
        ALTER TABLE "documents_template_versions" ADD COLUMN IF NOT EXISTS "letter_thumbnail_blurhash" VARCHAR(64);
        ALTER TABLE "documents_template_versions" ADD COLUMN IF NOT EXISTS "letter_preview_key" VARCHAR(512);
        ALTER TABLE "documents_template_versions" ADD COLUMN IF NOT EXISTS "letter_full_key" VARCHAR(512);
        ALTER TABLE "documents_generated_cvs" ADD COLUMN IF NOT EXISTS "letter_style" VARCHAR(8);
COMMENT ON COLUMN "documents_generated_cvs"."letter_style" IS 'Lettre : style servi, template ou plain (ADR-0036).';
        ALTER TABLE "applications_dossiers" ADD COLUMN IF NOT EXISTS "letter_style" VARCHAR(8);
COMMENT ON COLUMN "applications_dossiers"."letter_style" IS 'Style de lettre retenu (template ou plain), ou NULL (ADR-0036).';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_template_versions" DROP COLUMN IF EXISTS "letter_source_key";
        ALTER TABLE "documents_template_versions" DROP COLUMN IF EXISTS "letter_spec";
        ALTER TABLE "documents_template_versions" DROP COLUMN IF EXISTS "letter_sample";
        ALTER TABLE "documents_template_versions" DROP COLUMN IF EXISTS "letter_labels";
        ALTER TABLE "documents_template_versions" DROP COLUMN IF EXISTS "letter_thumbnail_key";
        ALTER TABLE "documents_template_versions" DROP COLUMN IF EXISTS "letter_thumbnail_blurhash";
        ALTER TABLE "documents_template_versions" DROP COLUMN IF EXISTS "letter_preview_key";
        ALTER TABLE "documents_template_versions" DROP COLUMN IF EXISTS "letter_full_key";
        ALTER TABLE "documents_generated_cvs" DROP COLUMN IF EXISTS "letter_style";
        ALTER TABLE "applications_dossiers" DROP COLUMN IF EXISTS "letter_style";"""
