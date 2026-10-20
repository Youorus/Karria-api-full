# Claude — karria_api_v2

@AGENTS.md

> ## ⚠️ Les directives de fond sont dans `AGENTS.md`, importé ci-dessus.
>
> Il contient le rôle attendu, l'architecture, les quatre règles, les
> quatorze interdits, le workflow obligatoire et le domaine métier. **Si
> l'import ne s'est pas résolu — tu ne vois pas ces sections dans ton
> contexte — lis `AGENTS.md` immédiatement avant toute autre action.**
>
> **Ce fichier-ci ne contient que les mécaniques propres à Claude Code.**
> Il ne répète aucune règle de fond : une règle dupliquée est une règle qui
> divergera.

---

## Commandes du projet

```bash
make install     # dépendances (uv) + hooks pre-commit
make dev         # API en rechargement à chaud sur :8000
make check       # lint + mypy strict + frontières + tests — IDENTIQUE à la CI
make test-unit   # tests rapides, sans I/O — à lancer en boucle pendant le dev
make boundaries  # contrats d'architecture seuls
make migrate m="description"   # génère une migration Aerich
```

**`make check` avant toute proposition de commit.** Sans exception.

## Spécificités d'usage

### Environnement Python

Le projet utilise **uv**, pas pip. N'active jamais un venv à la main et
n'appelle jamais `python` directement : passe par `uv run <commande>`, ou
mieux par les cibles du `Makefile`.

Si un shell hérite d'un `VIRTUAL_ENV` d'un autre projet, uv l'ignore avec un
avertissement — c'est sans conséquence. `VIRTUAL_ENV= make check` le fait
taire.

### Les trois dépôts voisins

- `~/PycharmProjects/karria_api` — la **V1**. En lecture seule : on y
  cherche ce que l'API faisait (routes, champs, données), jamais du code à
  reprendre. Ne la modifie pas.
- `~/WebstormProjects/karria_app` — l'**app** et son contrat
  (`src/lib/dev/serveur-simule.ts`, `docs/contrat-api-*.md`). Ne la
  modifie pas sans demande explicite.
- `~/PycharmProjects/zoumani_api` — le **cadre** dont ce projet est issu.
  Référence pour une question d'architecture ; ne la modifie jamais.

### Édition de fichiers

Utilise `Edit` pour les modifications ciblées. **Ne réécris pas un fichier
entier avec `Write` pour changer quelques lignes** : tu perds les
ajustements du formateur et tu rends la revue illisible.

Les hooks pre-commit reformatent au commit. Si `ruff format` a modifié un
fichier que tu venais d'écrire, c'est normal — relis-le avant de l'éditer à
nouveau.

### Sous-agents et outils

- Ne lance pas de sous-agent, de workflow ou de recherche multi-agents sans
  demande explicite. Ce projet se construit module par module, en dialogue.
- Pour comprendre du code existant, lis-le. Le socle tient en contexte.

### Vérification

Ne dis jamais « les tests passent » sans les avoir lancés. Si tu n'as pas pu
vérifier quelque chose (Docker non démarré, base absente, migration non
appliquée), **dis-le explicitement** dans ta réponse plutôt que de le passer
sous silence.

## Commits

Conventional Commits, en français, portée = nom du module. Le message est
vérifié par un hook `commit-msg`.

```
feat(<module>): ajoute le fil d'offres matchées
fix(<module>): corrige l'expiration du code de connexion
refactor(<module>): extrait le calcul de score dans le domaine
docs(adr): consigne le choix du fournisseur de modèle
chore(deps): met à jour FastAPI
```

Ne commite et ne pousse **que sur demande explicite**.

## Style de réponse

Réponds en français. Justifie techniquement. Contredis quand c'est pertinent.
Va droit au but : pas de récapitulatif de ce que tu viens de faire ligne à
ligne, mais l'essentiel, les choix, et ce qui reste non vérifié.
