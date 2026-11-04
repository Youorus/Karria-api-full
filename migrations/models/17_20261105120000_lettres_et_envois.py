from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `documents` (ADR-0030, ADR-0031) : un export est un CV **ou** une
# lettre (`kind`), rattaché ou non à un dossier de candidature (`dossier_id`,
# UUID libre posé par le module `applications`, sans clé étrangère — règle 4),
# avec l'entreprise destinataire d'une lettre pour nommer le fichier ; et le
# journal des envois par e-mail (`documents_sends`), sur lequel se compte le
# quota de cinq envois par jour. Idempotente, à appliquer **avant** le code :
# sans les colonnes, tout export répond 500.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "documents_generated_cvs"
            ADD COLUMN IF NOT EXISTS "kind" VARCHAR(8) NOT NULL DEFAULT 'cv',
            ADD COLUMN IF NOT EXISTS "dossier_id" UUID,
            ADD COLUMN IF NOT EXISTS "company_name" VARCHAR(200) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS "idx_documents__dossier_8b1c2e" ON "documents_generated_cvs" ("dossier_id");
COMMENT ON COLUMN "documents_generated_cvs"."kind" IS 'cv ou letter.';
COMMENT ON COLUMN "documents_generated_cvs"."dossier_id" IS 'Dossier de candidature (module applications).';
COMMENT ON COLUMN "documents_generated_cvs"."company_name" IS 'Entreprise destinataire d''une lettre.';
CREATE TABLE IF NOT EXISTS "documents_sends" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "generated_ids" JSONB NOT NULL,
    "to_address" VARCHAR(254) NOT NULL,
    "status" VARCHAR(16) NOT NULL DEFAULT 'queued',
    "sent_at" TIMESTAMPTZ,
    "error" VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS "idx_documents_s_user_id_4f2a91" ON "documents_sends" ("user_id");
COMMENT ON COLUMN "documents_sends"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "documents_sends"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "documents_sends"."generated_ids" IS 'Exports joints.';
COMMENT ON COLUMN "documents_sends"."to_address" IS 'Destinataire.';
COMMENT ON COLUMN "documents_sends"."status" IS 'queued, sent, failed.';
COMMENT ON COLUMN "documents_sends"."error" IS 'Raison technique d''un échec.';
COMMENT ON TABLE "documents_sends" IS 'Un envoi de documents par e-mail (journal).';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "documents_sends";
ALTER TABLE "documents_generated_cvs"
    DROP COLUMN IF EXISTS "company_name",
    DROP COLUMN IF EXISTS "dossier_id",
    DROP COLUMN IF EXISTS "kind";"""
