# Module `health` — référence canonique

Ce module est **le patron à copier** pour tout nouveau module. Il est
volontairement minuscule mais complet : il exerce les quatre couches, un
port, une inversion de dépendance et une vraie règle de domaine.

## Anatomie

```
health/
├── __init__.py            API publique + ModuleDescriptor. La seule porte d'entrée.
├── domain/
│   └── health.py          HealthStatus, ComponentHealth, SystemHealth.
│                          Règle d'agrégation pure. Importe uniquement la stdlib.
├── application/
│   ├── ports.py           HealthProbe — ce dont le use case a besoin.
│   └── use_cases.py       CheckSystemHealth — orchestration, parallélisme, délais.
├── infrastructure/
│   └── probes.py          DatabaseProbe — implémente le port avec PostgreSQL.
└── api/
    ├── schemas.py         Contrat HTTP, distinct du domaine.
    ├── dependencies.py    Câblage port → implémentation.
    └── routers.py         Routes. Recevoir, déléguer, projeter.
```

## Le sens de lecture

Les dépendances vont **vers l'intérieur**, sans exception :

```
api ──► application ──► domain
 │            ▲
 └──► infrastructure
```

`infrastructure` implémente un port défini dans `application` ; c'est
l'inversion de dépendance. `domain` ne pointe vers rien.

## Ce que ça achète, concrètement

- **La règle d'agrégation se teste sans rien monter.** `SystemHealth.status`
  est une fonction pure : trois `ComponentHealth` en entrée, un statut en
  sortie. Aucune base, aucun serveur, une milliseconde.
- **Le use case se teste avec une sonde factice de trois lignes.** Le port
  `HealthProbe` est un `Protocol` : n'importe quel objet qui a `name`,
  `critical` et `check()` fait l'affaire.
- **Remplacer PostgreSQL ne touche que `infrastructure/probes.py`.** Ni le
  domaine, ni le use case, ni la route ne bougent.

## Pourquoi il n'a pas de couche persistance

Parce qu'il n'a rien à stocker. Un module n'est pas obligé d'avoir les
quatre couches : il est obligé de ne pas les enjamber. Un module sans
persistance omet `infrastructure/` ; un module purement technique peut
n'avoir qu'une couche. Ce qui n'est jamais acceptable, c'est qu'une route
parle à l'ORM ou qu'un use case importe FastAPI.

## Créer un nouveau module

```bash
cp -r src/karria/modules/health src/karria/modules/<nom>
```

Puis **une ligne** dans `src/karria/bootstrap/modules.py` :

```python
from karria.modules.<nom> import MODULE as <nom>_module

ENABLED_MODULES = (health_module, <nom>_module)
```

Si le module possède des modèles, renseigner `models_module` dans son
`ModuleDescriptor` : Aerich les découvrira par le registre, sans qu'aucune
liste ait à être tenue à jour ailleurs.
