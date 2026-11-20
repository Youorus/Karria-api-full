from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Les oublis à terminer (audit de sécurité du 2026-11-20) : la suppression d'un
# compte y inscrit une ligne dans sa propre transaction ; le worker la rejoue
# jusqu'à ce que chaque module ait effacé ce qui concernait la personne (CV,
# offres, documents, abonnement Stripe, fil du support…). Table neuve, vide.
# Idempotente ; à appliquer **avant** le code (sans la table, supprimer son
# compte répond 500).


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "identity_pending_forgets" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL UNIQUE,
    "attempts" INT NOT NULL DEFAULT 0,
    "last_error" VARCHAR(300)
);
COMMENT ON COLUMN "identity_pending_forgets"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "identity_pending_forgets"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "identity_pending_forgets"."user_id" IS 'Le compte supprimé.';
COMMENT ON COLUMN "identity_pending_forgets"."attempts" IS 'Rejeux qui ont échoué.';
COMMENT ON COLUMN "identity_pending_forgets"."last_error" IS 'Dernier échec.';
COMMENT ON TABLE "identity_pending_forgets" IS 'Comptes supprimés dont l''oubli complet reste à confirmer.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "identity_pending_forgets";"""
