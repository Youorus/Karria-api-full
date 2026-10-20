# karria_api_v2

Backend de Karria, plateforme d'emploi assistée par IA. Refonte de la V1
(`karria_api`) sur le cadre de `zoumani_api` : monolithe modulaire FastAPI,
Python 3.13, Tortoise ORM, PostgreSQL.

**État : socle technique seul.** Un module (`health`), aucun module métier.
Les modules arrivent un par un, sur décision de Marc.

## Démarrage

Prérequis : **Docker** (PostgreSQL et MinIO tournent en conteneur) et
[**uv**](https://docs.astral.sh/uv/) (`brew install uv`). Pas besoin
d'installer Python : uv récupère la 3.13 tout seul.

```bash
make install                    # dépendances + hooks pre-commit
cp .env.example .env            # configuration locale (jamais versionnée)

make db-up                      # PostgreSQL + MinIO
docker compose exec postgres createdb -U karria karria_test   # base des tests, une fois

make storage-provision          # crée les buckets déclarés au registre
make dev                        # http://localhost:8000/docs
```

Au quotidien : `make db-up` puis `make dev`. `make migrate-apply` s'ajoute
le jour où un module possède des tables — il n'y a pas encore de migration.

### Où aller une fois lancé

| URL | |
|---|---|
| **http://localhost:8000/docs** | Documentation interactive de l'API |
| http://localhost:8000/health/ready | Sondes : base de données et stockage |
| http://localhost:9001 | Console MinIO (`minioadmin` / `minioadmin`) |

**`http://localhost:8000/` répond `404`, et c'est normal** : aucune route
n'est déclarée à la racine. Les routes métier vivent sous `/api/v1`. Seules
les sondes de santé sont hors préfixe — elles s'adressent à l'orchestrateur.

### Vérifier son travail

Une seule commande garantit que le code est mergeable — c'est **exactement**
ce que lance la CI :

```bash
make check       # lint + format + mypy strict + frontières + tests + couverture
```

Pendant le développement, `make test-unit` tourne en quelques dixièmes de
seconde et ne demande ni base, ni stockage, ni réseau.

### En cas de souci

| Symptôme | Cause probable |
|---|---|
| `No TortoiseContext is currently active` | Base non connectée : `make db-up` |
| Tests d'intégration ignorés | Aucune migration encore, ou MinIO éteint : `make db-up` puis `make storage-provision` |
| `connection refused` sur le port 5432 | Un autre PostgreSQL occupe le port. `brew services stop postgresql@17` |
| Une route protégée répond `401` | Attendu : aucun émetteur de jeton n'existe encore (ADR-0007) |

## Architecture

Monolithe **modulaire** : un seul déployable, mais des modules assez découplés
pour qu'un contexte métier puisse être extrait plus tard sans réécriture.

```
src/karria/
├── main.py            Point d'entrée ASGI. Ne connaît aucun module métier.
├── bootstrap/         Assemblage de l'application.
│   ├── modules.py     ⭐ Registre déclaratif : la SEULE liste des modules.
│   └── wiring.py      Branchements synchrones entre modules.
├── shared/            Briques techniques transverses. Zéro métier.
│   ├── config/        Settings composés par domaine, injectés.
│   ├── auth/          Principal authentifié et permissions (le mécanisme).
│   ├── database/      Tortoise, UnitOfWork, repository de base.
│   ├── storage/       Stockage objet S3, portable entre fournisseurs.
│   ├── images/        Normalisation des images reçues.
│   ├── mail/          Envoi d'e-mails : registre, gabarits, SMTP.
│   ├── errors/        Erreurs typées et leur traduction HTTP.
│   ├── logging/       Logs structurés + correlation_id.
│   ├── http/          Middlewares, pagination, client sortant.
│   ├── events/        Bus d'événements in-process.
│   └── module.py      Contrat qu'un module expose à l'assemblage.
└── modules/           Un dossier = un contexte métier.
    └── health/        Le module canonique, à copier.
```

Les quatre règles d'architecture sont **vérifiées en CI** par `import-linter`
(`make boundaries`). Elles sont détaillées dans `AGENTS.md` §3.

### Ajouter un module

1. Copier `src/karria/modules/health/` — c'est la **référence canonique**.
2. Ajouter **une ligne** dans `bootstrap/modules.py`.
3. L'inscrire aux contrats d'architecture de `pyproject.toml`.

`main.py` n'est jamais touché.

## Documentation

- `AGENTS.md` — **directives de travail**, valables pour les humains comme
  pour les agents de code : rôle attendu, règles, interdits, workflow,
  domaine métier et ses deux références (la V1 pour le fond, l'app pour le
  contrat). `CLAUDE.md` et `GEMINI.md` y renvoient.
- `src/karria/modules/health/README.md` — le module canonique, commenté
  couche par couche. **À lire en premier** avant d'écrire un module.
- `docs/adr/` — les décisions d'architecture et **pourquoi** elles ont été
  prises. Toutes techniques à ce stade, reprises du cadre `zoumani_api`.
- `docs/DOMAIN.md` — les surfaces du contrat de l'app. Le découpage en
  contextes n'est **pas** écrit.
- `docs/CONVENTIONS.md` — nommage, structure, style. Non négociable.
- `docs/PLUS-TARD.md` — ce qui a été repoussé, et pourquoi.
- `docs/DEPLOIEMENT.md` — la séquence de mise en production, à écrire au
  premier déploiement.
