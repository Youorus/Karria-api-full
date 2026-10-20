# ADR-0006 — uv et `pyproject.toml` unique

**Statut :** Acceptée — 2026-10-20, reprise du cadre `zoumani_api`

## Contexte

La V1 accumulait `requirements.in`, `requirements.txt`,
`requirements-dev.in`, `requirements-dev.txt`, `requirements_full.txt` et
`requirements.txt.bak` — six fichiers, trois vérités contradictoires.

Sa configuration d'outillage était dans le même état : `mypy.ini` **et**
`[tool.mypy]` dans `pyproject.toml`, avec des valeurs divergentes
(`strict = true` d'un côté, absent de l'autre). Personne ne pouvait dire
lequel s'appliquait réellement. Black et ruff-format étaient tous deux
configurés, pour le même travail.

## Décision

- **uv** pour les dépendances et l'environnement virtuel, avec `uv.lock`
  versionné.
- **`pyproject.toml` comme source de vérité unique** : dépendances, ruff,
  mypy, pytest, couverture, aerich, import-linter. **Aucun** autre fichier
  de configuration d'outil à la racine.
- **ruff seul** pour le lint et le formatage. Pas de Black, pas d'isort,
  pas de flake8.
- **`make check`** reproduit exactement la CI.

## Justification

- **Un lock déterministe, multi-plateforme.** `pip-tools` n'en produit pas
  vraiment ; c'est ce qui a permis à six fichiers de diverger dans la V1.
- **Vitesse.** Résolution et installation d'un ordre de grandeur plus
  rapides que pip ou Poetry, ce qui compte à chaque exécution de CI.
- **Une seule vérité par sujet.** Deux fichiers de configuration pour le
  même outil, c'est un bug en attente : celui qui est lu n'est pas celui
  qu'on lit.
- **`--frozen` en CI** (`UV_FROZEN=1`) : la CI échoue si `uv.lock` ne
  correspond plus à `pyproject.toml`. Impossible de déployer un jeu de
  dépendances non verrouillé.

## Conséquences

- Une seule commande pour installer : `make install`.
- Le groupe `dev` (PEP 735) n'est jamais embarqué dans l'image Docker.
- uv devient un prérequis de la machine de développement — installation en
  une commande, aucun venv à gérer à la main.

## Note d'application

Deux règles de lint envisagées ont été **délibérément écartées** :

- **`TC` (flake8-type-checking)** — déplacerait les imports dans des blocs
  `TYPE_CHECKING` pour accélérer le démarrage. Mais Pydantic et FastAPI
  résolvent les annotations à l'exécution : les imports ainsi déplacés
  produisent des `NameError` au premier appel. Le contourner par une liste
  de classes « évaluées à l'exécution » revient à maintenir un inventaire
  qui se désynchronise en silence. Le gain ne vaut pas le risque.

En revanche, `filterwarnings = ["error"]` **est** activé dans pytest : un
avertissement de dépréciation ignoré devient une rupture au prochain
changement de version majeure. Il a d'ailleurs immédiatement révélé deux
dépréciations réelles au premier passage de la suite.

## Alternatives écartées

- **Poetry** — plus mature, mais nettement plus lent, et sa résolution
  devient pénible sur les gros graphes de dépendances.
- **pip-tools** — c'est précisément ce qui a produit les six fichiers
  divergents de la V1.
