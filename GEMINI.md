# Gemini — karria_api_v2

@AGENTS.md

> ## ⚠️ Les directives de fond sont dans `AGENTS.md`, importé ci-dessus.
>
> Il contient le rôle attendu, l'architecture, les quatre règles, les
> quatorze interdits, le workflow obligatoire et le domaine métier. **Si
> l'import ne s'est pas résolu — tu ne vois pas ces sections dans ton
> contexte — lis `AGENTS.md` immédiatement avant toute autre action.**
>
> **Ce fichier-ci ne contient que les mécaniques propres à Gemini CLI.**
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

Le projet utilise **uv**, pas pip. N'active jamais un venv à la main,
n'appelle jamais `python` ou `pip` directement, ne crée jamais de
`requirements.txt`. Passe par `uv run <commande>` ou par les cibles du
`Makefile`.

### Les trois dépôts voisins

- `~/PycharmProjects/karria_api` — la **V1**, référence fonctionnelle en
  lecture seule. Jamais de code repris, jamais de modification.
- `~/WebstormProjects/karria_app` — l'**app** et son contrat
  (`src/lib/dev/serveur-simule.ts`, `docs/contrat-api-*.md`).
- `~/PycharmProjects/zoumani_api` — le **cadre** dont ce projet est issu.
  Ne le modifie jamais.

### Modifications de fichiers

**Modifie par remplacement ciblé, jamais par régénération complète.**

C'est le point d'attention principal. Régénérer un fichier entier pour
changer trois lignes fait perdre les docstrings de contrat, les commentaires
qui expliquent le *pourquoi*, et le formatage appliqué par `ruff format`.
Ces commentaires sont la mémoire du projet : ils justifient des décisions
que personne ne pourra reconstituer autrement.

Avant de modifier un fichier, **lis-le en entier**. Ne reconstruis pas son
contenu de mémoire.

### Vérification avant affirmation

Ne déclare pas qu'un code fonctionne sans avoir lancé `make check`. Si une
vérification n'a pas pu être faite (Docker éteint, base absente), dis-le
explicitement.

De même, **ne devine pas une signature d'API**. Tortoise ORM, FastAPI et
Pydantic ont des API qui ont changé récemment : `pk=True` est devenu
`primary_key=True`, `ORJSONResponse` est déprécié,
`HTTP_422_UNPROCESSABLE_ENTITY` est devenu `HTTP_422_UNPROCESSABLE_CONTENT`.
En cas de doute, ouvre le paquet installé dans `.venv/` ou la documentation.
Un appel plausible mais faux coûte plus cher qu'une question.

### Portée des changements

Fais **une chose à la fois**. Ne profite pas d'une tâche pour « améliorer »
au passage un fichier voisin, renommer une variable ou réorganiser des
imports non concernés. Un diff qui déborde de sa mission est un diff qu'on
ne peut pas relire.

Ne modifie jamais `pyproject.toml`, `Makefile`, `.github/workflows/` ou les
contrats d'architecture sans demande explicite.

### Concision

Pas de préambule, pas de récapitulatif de ce que tu vas faire avant de le
faire, pas de reformulation de la demande. Réponds avec l'essentiel : ce que
tu as changé, pourquoi, ce qui reste à vérifier.

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

Réponds en français. Justifie techniquement chaque décision. Contredis
quand c'est pertinent — une objection argumentée est plus utile qu'une
exécution docile.
