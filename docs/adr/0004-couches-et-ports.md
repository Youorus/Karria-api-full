# ADR-0004 — Découpage en couches avec ports

**Statut :** Acceptée — 2026-10-20, reprise du cadre `zoumani_api`

## Contexte

Dans la V1, les services appelaient directement l'ORM. Conséquences
mesurables : `ProfileService` fait 1 126 lignes et ne peut pas être testé
sans une base réelle ; la logique métier est soudée à Tortoise ; la
« couche service » n'existe que de nom.

Trois profondeurs d'architecture étaient envisageables : couches simples,
couches avec ports, ou hexagonal complet avec entités de domaine séparées.

## Décision

**Couches avec ports.** Chaque module se structure ainsi :

```
api ──► application ──► domain
 │            ▲
 └──► infrastructure
```

- `domain/` — règles métier pures. N'importe que la bibliothèque standard
  et `shared.errors`.
- `application/` — use cases et **ports** (`Protocol`) décrivant ce dont
  ils ont besoin. Aucun ORM, aucun HTTP.
- `infrastructure/` — implémente les ports. Seule couche qui connaît
  Tortoise, le réseau et les services tiers.
- `api/` — routes FastAPI et schémas HTTP. Reçoit, délègue, projette.

Les modèles Tortoise restent des **modèles de persistance** : on
n'introduit pas d'entités de domaine séparées avec des mappers, sauf si un
module précis le justifie.

## Justification

- **Le port est ce qui rend le test unitaire possible.** Le use case
  `CheckSystemHealth` du module `health` se teste avec une sonde factice de
  dix lignes, sans base ni serveur. C'est exactement ce que la V1 ne
  permettait pas.
- **On utilise `Protocol` plutôt qu'une classe abstraite** : l'implémentation
  n'hérite de rien, donc n'importe rien de la couche `application`. Le
  découplage vaut dans les deux sens.
- **Pas d'entités de domaine séparées au départ.** Un module CRUD paierait
  des mappers pour zéro règle métier. La montée en hexagonal complet reste
  ouverte, module par module, quand la richesse du domaine la justifie.

## Conséquences

**Positives**

- Les règles métier se testent en millisecondes.
- Remplacer une dépendance externe ne touche que `infrastructure/`.
- Un développeur sait immédiatement où poser un nouveau bout de code.

**Négatives, assumées**

- Plus de fichiers pour un module simple. C'est le prix de la frontière ;
  un module trivial peut omettre les couches dont il n'a pas l'usage —
  mais jamais les enjamber.
- Les schémas HTTP dupliquent partiellement les objets de domaine. Cette
  duplication est délibérée : elle rend visible tout changement cassant du
  contrat public, à l'endroit où il l'est.

## Alternatives écartées

- **Couches simples sans ports** — rapide à écrire, mais les use cases
  restent couplés aux implémentations concrètes. C'est une V1 mieux
  rangé, pas une V1 mieux architecturé.
- **Hexagonal complet dès le départ** — environ deux fois plus de code et
  des mappers à maintenir jusque pour du CRUD trivial. Réservé aux modules
  dont le domaine le mérite.
