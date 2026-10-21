from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "identity_departure_feedback" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "reason" VARCHAR(32) NOT NULL,
    "details" VARCHAR(1000),
    "account_age_days" INT NOT NULL,
    "platform" VARCHAR(16),
    "app_version" VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS "idx_identity_de_reason_5bd9a7" ON "identity_departure_feedback" ("reason");
COMMENT ON COLUMN "identity_departure_feedback"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "identity_departure_feedback"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "identity_departure_feedback"."reason" IS 'Raison choisie dans l''app.';
COMMENT ON COLUMN "identity_departure_feedback"."details" IS 'Mot libre de la personne, blancs ôtés. Peut contenir ce qu''elle a voulu y mettre.';
COMMENT ON COLUMN "identity_departure_feedback"."account_age_days" IS 'Âge du compte à la suppression, en jours.';
COMMENT ON COLUMN "identity_departure_feedback"."platform" IS 'Plateforme de l''appareil principal, si connue.';
COMMENT ON COLUMN "identity_departure_feedback"."app_version" IS 'Version de l''app sur cet appareil, si connue.';
COMMENT ON TABLE "identity_departure_feedback" IS 'Pourquoi les gens partent. Anonyme : aucun lien vers un compte.';
        COMMENT ON COLUMN "identity_login_challenges"."user_id" IS 'Compte visé s''il existait déjà. Sans clé étrangère.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        COMMENT ON COLUMN "identity_login_challenges"."user_id" IS NULL;
        DROP TABLE IF EXISTS "identity_departure_feedback";"""


MODELS_STATE = (
    "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"
)
