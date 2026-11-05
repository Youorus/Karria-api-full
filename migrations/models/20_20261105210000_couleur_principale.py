from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# ADR-0034 : la couleur principale d'un CV, choisie par la personne. Trois
# colonnes NULL (`#RRGGBB`, 7 caractères) : le choix par défaut
# (`documents_selections`), le dossier de candidature (`applications_dossiers`)
# et la couleur avec laquelle un export a été rendu (`documents_generated_cvs`,
# pour que « Mes CV » la montre et la réutilise). Idempotente ; à appliquer
# **avant** le code (sans les colonnes, la lecture d'un dossier ou du choix
# répond 500).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_selections" ADD COLUMN IF NOT EXISTS "primary_color" VARCHAR(7);
COMMENT ON COLUMN "documents_selections"."primary_color" IS 'Couleur principale choisie (#RRGGBB), ou NULL.';
        ALTER TABLE "documents_generated_cvs" ADD COLUMN IF NOT EXISTS "primary_color" VARCHAR(7);
COMMENT ON COLUMN "documents_generated_cvs"."primary_color" IS 'Couleur principale du rendu (#RRGGBB), ou NULL.';
        ALTER TABLE "applications_dossiers" ADD COLUMN IF NOT EXISTS "primary_color" VARCHAR(7);
COMMENT ON COLUMN "applications_dossiers"."primary_color" IS 'Couleur principale posée sur le modèle du dossier (#RRGGBB), ou NULL.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_selections" DROP COLUMN IF EXISTS "primary_color";
        ALTER TABLE "documents_generated_cvs" DROP COLUMN IF EXISTS "primary_color";
        ALTER TABLE "applications_dossiers" DROP COLUMN IF EXISTS "primary_color";"""
