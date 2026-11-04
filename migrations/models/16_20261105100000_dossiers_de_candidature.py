from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Module `applications` (ADR-0033) : les dossiers de candidature. Une table,
# un dossier par (compte, offre) : l'offre figée en JSON (`offer_snapshot`,
# lisible même si l'offre quitte l'index), la copie adaptée du CV, les
# propositions de Karria avec leur état, la lettre, la couverture des
# mots-clés. `user_id` et `offer_id` sans clé étrangère (règle 4).
# Idempotente, à appliquer **avant** le code : sans la table,
# `GET /applications` répond 500.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "applications_dossiers" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "user_id" UUID NOT NULL,
    "offer_id" UUID NOT NULL,
    "offer_snapshot" JSONB NOT NULL,
    "template_id" UUID,
    "cv" JSONB NOT NULL,
    "cv_base_hash" VARCHAR(64) NOT NULL,
    "propositions" JSONB NOT NULL,
    "letter" JSONB,
    "coverage" JSONB NOT NULL,
    "status" VARCHAR(16) NOT NULL,
    "adaptation" VARCHAR(16) NOT NULL,
    "adaptation_error" VARCHAR(64),
    "applied_at" TIMESTAMPTZ,
    CONSTRAINT "uid_application_user_id_3a7f12" UNIQUE ("user_id", "offer_id")
);
CREATE INDEX IF NOT EXISTS "idx_application_user_id_9c2b4e" ON "applications_dossiers" ("user_id");
CREATE INDEX IF NOT EXISTS "idx_application_user_id_updated_5d8e1f" ON "applications_dossiers" ("user_id", "updated_at");
COMMENT ON COLUMN "applications_dossiers"."created_at" IS 'Création (UTC).';
COMMENT ON COLUMN "applications_dossiers"."updated_at" IS 'Dernière modification (UTC).';
COMMENT ON COLUMN "applications_dossiers"."user_id" IS 'Le compte. Pas de clé étrangère : `applications` ne lit pas `identity`.';
COMMENT ON COLUMN "applications_dossiers"."offer_id" IS 'L''offre visée (module `offers`), sans clé étrangère.';
COMMENT ON COLUMN "applications_dossiers"."offer_snapshot" IS 'L''offre figée à la création : titre, entreprise, logo, ville, contrat, texte, mots-clés.';
COMMENT ON COLUMN "applications_dossiers"."template_id" IS 'Le modèle de CV choisi (module `documents`), ou aucun.';
COMMENT ON COLUMN "applications_dossiers"."cv" IS 'La copie adaptée du CV, au format `GET /cv/me`.';
COMMENT ON COLUMN "applications_dossiers"."cv_base_hash" IS 'Condensat du CV de base copié : dit s''il a changé depuis.';
COMMENT ON COLUMN "applications_dossiers"."propositions" IS 'Les propositions de Karria : chemin, avant, après, raison, état.';
COMMENT ON COLUMN "applications_dossiers"."letter" IS 'La lettre de motivation : destinataire, objet, paragraphes, formule.';
COMMENT ON COLUMN "applications_dossiers"."coverage" IS 'Mots-clés de l''offre présents et absents du CV adapté.';
COMMENT ON COLUMN "applications_dossiers"."status" IS 'brouillon, pret ou candidate.';
COMMENT ON COLUMN "applications_dossiers"."adaptation" IS 'en_cours, faite ou echouee.';
COMMENT ON COLUMN "applications_dossiers"."adaptation_error" IS 'La raison d''un échec d''adaptation, courte.';
COMMENT ON COLUMN "applications_dossiers"."applied_at" IS 'Quand la personne a dit avoir candidaté.';
COMMENT ON TABLE "applications_dossiers" IS 'Les dossiers de candidature : offre figée, CV adapté, lettre, propositions.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "applications_dossiers";"""
