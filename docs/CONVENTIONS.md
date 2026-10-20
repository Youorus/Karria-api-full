# Conventions

Ces conventions ne sont pas des préférences esthétiques. Chacune corrige un
problème observé dans la V1. Elles ne sont pas négociables au cas par cas :
une convention appliquée à 80 % ne sert à rien, puisqu'il faut alors vérifier
chaque fichier.

## 1. Nommage des fichiers

La V1 mélangeait `router.py` et `routers.py`, `schema.py` et `schemas.py`,
plus des variantes comme `public_router.py`, `machine_router.py`,
`user_router.py`. Personne ne pouvait deviner où se trouvait quoi.

Les noms de fichiers dans un module sont **fixés** :

| Fichier | Couche | Contenu |
|---------|--------|---------|
| `domain/<concept>.py` | domain | Entités, value objects, règles pures |
| `application/ports.py` | application | `Protocol` décrivant les besoins |
| `application/use_cases.py` | application | Un cas d'usage = une intention |
| `infrastructure/models.py` | infrastructure | Modèles Tortoise |
| `infrastructure/repositories.py` | infrastructure | Implémentations des ports |
| `api/routers.py` | api | Routes FastAPI |
| `api/schemas.py` | api | Schémas de requête et de réponse |
| `api/dependencies.py` | api | Câblage port → implémentation |

**Toujours au pluriel** quand le fichier contient une collection
(`schemas.py`, `routers.py`, `ports.py`). Aucune variante.

## 2. Langue

- **Code en anglais** : noms de classes, fonctions, variables, champs,
  événements. C'est la langue des bibliothèques qu'on utilise ; mélanger
  produit du `get_utilisateur_by_email`.
- **Documentation et commentaires en français** : docstrings, ADR, README.
  C'est la langue dans laquelle on raisonne le mieux.
- **Messages d'erreur destinés à l'utilisateur en français.**
- **Clés de log en anglais**, en `domaine.action` : `user.created`,
  `http.access`, `event_bus.handler_failed`.

## 3. Journalisation

On journalise des **paires clé/valeur**, jamais des phrases interpolées.

```python
logger.info("order.shipped", order_id=str(order.id), carrier=carrier.name)  # ✅
logger.info(f"Commande {order.id} expédiée via {carrier.name}")  # ❌
```

La première est requêtable dans un agrégateur, la seconde ne l'est pas.
Interdit par `T20` : `print()` ne passe pas le lint.

## 4. Docstrings

Convention Google, en français. Le module et les fonctions publiques ont une
docstring qui explique **le contrat et le pourquoi**, pas la paraphrase du
code.

```python
def atomic(self) -> AbstractAsyncContextManager[None]:
    """Ouvre une transaction ; valide en sortie, annule en cas d'exception."""  # ✅
    """Cette méthode ouvre une transaction."""  # ❌
```

L'en-tête de module au format « Contrat » — repris de la V1, une de ses
bonnes idées — est attendu sur tout fichier non trivial.

## 5. Erreurs

- Le domaine lève des exceptions **typées** de `karria.shared.errors`.
- **Jamais** de `HTTPException` en dehors de `shared/errors/handlers.py`.
- **Jamais** de `raise ValueError` pour une erreur métier.
- Un nouveau code d'erreur s'ajoute dans `ErrorCode` **et** dans
  `STATUS_BY_ERROR_CODE` — un test échoue sinon.

## 6. Typage

`mypy --strict` sur `src/`, sans exception tolérée. Un `# type: ignore` doit
porter son code (`# type: ignore[arg-type]`) — les ignore nus sont refusés
par la configuration.

## 7. Tests

- `tests/unit/` — aucune I/O. Doit tourner en millisecondes.
- `tests/integration/` — dépendances réelles, marqué `@pytest.mark.integration`.
- `tests/e2e/` — traverse la pile HTTP.
- **Noms de tests en français, descriptifs.** `test_un_composant_non_critique_defaillant_degrade_sans_retirer_du_trafic`
  dit ce qui est garanti ; `test_health_2` ne dit rien.
- Un test par comportement. La V1 avait des fichiers de test « audit » de
  1 700 lignes : quand ils échouent, on ne sait pas ce qui est cassé.

## 8. Commits

Conventional Commits, en français, vérifiés par `pre-commit` sur le message.

```
feat(billing): ajoute le calcul de la TVA par pays
fix(auth): corrige l'expiration des jetons de rafraîchissement
refactor(matching): extrait le scoring dans un use case dédié
docs(adr): consigne le choix du bus d'événements
chore(deps): met à jour FastAPI en 0.141
```

Types : `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.
La portée est le nom du module.

## 9. Ce qu'on ne met jamais à la racine

La V1 avait une quarantaine de scripts, quatre PDF et deux captures
d'écran à la racine du dépôt.

- Un script d'exploitation va dans `scripts/`.
- Un fichier temporaire ne se commite pas.
- Un binaire ne se commite pas.
- `openapi.json` est **généré**, jamais versionné.
