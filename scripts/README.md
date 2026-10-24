# Scripts

Tout l'outillage d'exploitation vit ici, jamais à la racine du dépôt.

La V1 en avait une quarantaine à la racine — `fix_trigger.py`,
`update_models.py`, `db_cleanup.py`, `test_email_only.py` — mêlés aux
fichiers du projet, sans qu'on puisse distinguer un script jetable d'un
outil de production.

## Règles

- Un script est **jetable** ou **durable**. S'il est jetable, il ne se
  commite pas.
- Un script durable a une docstring en tête expliquant ce qu'il fait, quand
  l'utiliser et ce qu'il modifie.
- Un script qui écrit en base doit être **idempotent** et proposer un mode
  simulation (`--dry-run`).
- Un script n'est pas un module : il importe l'application, il ne
  la contourne pas.

## Ce qu'il y a aujourd'hui

| Script | Commande | Rôle |
|---|---|---|
| `doctor.py` | `make doctor` | Dit si l'environnement est réellement opérationnel : secrets, base, migrations, buckets, envoi d'e-mails. |
| `storage_provision.py` | `make storage-provision` | Crée et configure les buckets déclarés au registre. Idempotent, à relancer à chaque déploiement. |
| `test_smtp.py` | `uv run python scripts/test_smtp.py <adresse>` | Envoie un vrai message par le chemin de production, pour vérifier la configuration SMTP avant le premier code de connexion. |
| `migrate_v1.py` | `uv run python -m scripts.migrate_v1 export\|convert\|import` | Reprend les comptes V1 et leurs critères de recherche : export de la sauvegarde V1 (adresse, jeton push, matière des critères — rien d'autre), conversion en `SearchCriteria` V2 (déterministe, `--llm` pour affiner), import **simulé par défaut** (`--apply` pour écrire). Idempotent. |
| `push_campaign.py` | `uv run python -m scripts.push_campaign --in … --ledger …` | Campagne « Karria est de retour » aux anciens de la V1 qui voulaient des notifications. **Simule par défaut** ; `--send` envoie, une fois par personne grâce au registre local. |

## Ce qui tourne tout seul — règle à retenir

Un script périodique — purge, relance, rappel — n'a de valeur que si une
horloge le déclenche. Un script écrit, testé et jamais planifié est pire
qu'un script absent : il donne l'illusion que la fonctionnalité existe.
Le jour où un tel script apparaît, son planificateur (`systemd` ou
équivalent) arrive dans le même commit, sous `scripts/systemd/`.

**Pourquoi pas dans le `lifespan` de l'application** : ce qui s'exécute
au démarrage s'exécute à chaque redéploiement et sur chaque réplique
(AGENTS.md §6.4). La V1 ré-indexait son corpus à chaque boot ; les
tâches partiraient en double à chaque livraison.

## `push_receipts.py` — lire les reçus des notifications

    uv run python -m scripts.push_receipts

À planifier toutes les quinze minutes. Interroge Expo sur les tickets émis
depuis plus d'un quart d'heure et détache les jetons que le système a
déclarés morts (`DeviceNotRegistered`). Sans lui, les désinstallations
tardives ne seraient jamais vues et Expo finirait par bannir l'expéditeur
(ADR-0012).

## `migrate_v1.py` et `push_campaign.py` — reprendre les comptes de la V1

Trois étapes, trois fichiers, pour relire chaque résultat avant l'étape
suivante ; aucune ne touche la production V1 (la sauvegarde est restaurée
dans une base de travail) :

    uv run python -m scripts.migrate_v1 export  --dsn postgresql://… --out v1_export.json
    uv run python -m scripts.migrate_v1 convert --in v1_export.json --out v1_criteria.json [--llm]
    uv run python -m scripts.migrate_v1 import  --in v1_criteria.json            # simulation
    uv run python -m scripts.migrate_v1 import  --in v1_criteria.json --apply    # écrit
    uv run python -m scripts.push_campaign --in v1_criteria.json --ledger campagne.json [--send]

Ce que l'import **ne reprend pas**, et pourquoi : les comptes dont
l'adresse n'était pas vérifiée (un compte V2 n'existe qu'avec une adresse
prouvée) ; les jetons push FCM bruts — tous les jetons de la sauvegarde du
2026-09-12 le sont — qu'Expo ne sait pas acheminer (`docs/PLUS-TARD.md`).
Les fichiers intermédiaires portent des adresses : ils ne se commitent pas
et s'effacent après usage. Les sorties ne contiennent que des comptes.

- `templates.py` — modèles de CV (ADR-0020) : `submit` (dépôt d'un PDF, `--inline` pour analyser sur place), `ingest`, `list` (versions, fidélité, verdict, polices).
