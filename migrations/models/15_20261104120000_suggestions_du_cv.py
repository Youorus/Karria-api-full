from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `cv` (ADR-0028) : les suggestions à la saisie. Une table de
# vocabulaire matérialisée chaque nuit par le worker depuis l'index d'offres
# (intitulés, entreprises, villes), lue par préfixe sur la clé pliée
# `folded`. L'index `varchar_pattern_ops` sert `LIKE 'q%'` quelle que soit la
# collation de la base ; Tortoise ne sait pas le déclarer, il n'existe
# qu'ici. Idempotente, à appliquer **avant** le code : sans la table,
# `GET /cv/suggestions?kind=city` répond 500.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "cv_suggestions" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "kind" VARCHAR(16) NOT NULL,
    "label" VARCHAR(150) NOT NULL,
    "folded" VARCHAR(150) NOT NULL,
    "weight" INT NOT NULL,
    CONSTRAINT "uid_cv_suggesti_kind_1c9f0e" UNIQUE ("kind", "folded")
);
CREATE INDEX IF NOT EXISTS "idx_cv_suggesti_kind_folded_pattern" ON "cv_suggestions" ("kind", "folded" varchar_pattern_ops);
COMMENT ON COLUMN "cv_suggestions"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "cv_suggestions"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "cv_suggestions"."kind" IS 'title, company ou city.';
COMMENT ON COLUMN "cv_suggestions"."label" IS 'La forme proposée.';
COMMENT ON COLUMN "cv_suggestions"."folded" IS 'Minuscules, sans accents : la clé de recherche.';
COMMENT ON COLUMN "cv_suggestions"."weight" IS 'Occurrences dans l''index : le poids du classement.';
COMMENT ON TABLE "cv_suggestions" IS 'Vocabulaire des suggestions à la saisie du CV (intitulés, entreprises, villes).';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "cv_suggestions";"""
