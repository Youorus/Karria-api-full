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
