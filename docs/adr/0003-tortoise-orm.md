# ADR-0003 — Tortoise ORM et Aerich

**Statut :** Acceptée, **avec réserve consignée** — 2026-10-20, reprise du cadre `zoumani_api`

## Contexte

La V1 utilise Tortoise ORM et Aerich. Le choix pour Karria se posait
entre conserver cette pile, migrer vers SQLAlchemy 2.0 avec Alembic, ou
adopter SQLModel.

## Décision

**Tortoise ORM + Aerich**, par continuité avec la V1.

## Justification

- Maîtrise existante de la pile : pas de courbe d'apprentissage, pas de
  ralentissement au démarrage du projet.
- API asynchrone native, cohérente avec FastAPI.
- Modèles concis et lisibles, proches de l'ORM Django.

## Réserve consignée

Cette décision a été prise **contre la recommandation initiale**, qui
portait sur SQLAlchemy 2.0 + Alembic. La réserve est enregistrée ici pour
que le futur lecteur sache que le risque était connu et assumé :

1. **Aerich est le maillon faible sur les migrations non triviales.** Dans
   la V1, il a fallu écrire à la main `fix_trigger.py`,
   `fix_trigger_profile.py` et `db_migrate.py` pour rattraper ce qu'Aerich
   ne générait pas correctement. Rien n'indique que ce soit résolu.
2. **Le typage de Tortoise est plus faible** que celui de SQLAlchemy 2.0,
   ce qui limite le bénéfice réel de `mypy --strict` sur la couche
   persistance.
3. **L'écosystème est nettement plus restreint** : moins d'extensions,
   moins de réponses disponibles sur les cas difficiles.

## Ce qui rend cette réserve supportable

Le découpage en couches avec ports (ADR-0004) **confine Tortoise à
`infrastructure/`**. Aucun use case, aucune règle de domaine, aucune route
n'importe l'ORM — c'est vérifié en CI par l'ADR-0005.

Conséquence directe : si Aerich devient bloquant, la migration vers
SQLAlchemy touche les fichiers `infrastructure/repositories.py` et
`shared/database/`. Ni le domaine, ni les use cases, ni l'API ne bougent.
Le coût de sortie est borné, et il est borné **par construction**.

## Signal qui déclencherait la révision de cette ADR

Une migration qu'Aerich ne sait pas générer et qui exige d'écrire du SQL de
rattrapage à la main **deux fois** — la première fois est un accident, la
seconde est une tendance.

## Alternatives écartées

- **SQLAlchemy 2.0 + Alembic** — techniquement supérieur sur le typage et
  sur les migrations, écarté au profit de la continuité et de la vélocité.
- **SQLModel** — fusionne délibérément modèle de persistance et schéma
  d'API, c'est-à-dire précisément la frontière que cette architecture
  s'attache à maintenir nette. Maintenance également irrégulière.
