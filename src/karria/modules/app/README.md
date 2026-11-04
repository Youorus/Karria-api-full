# Module `app` — version minimale de l'application

## Ce qu'il possède

- La règle « cette version de l'app peut-elle encore servir ? »
  (`domain/release.py`) : `ok`, `update_available` ou `update_required`, à
  partir du numéro de build installé et des deux seuils de l'environnement.
- La route publique `GET /api/v1/app/release?platform=ios|android&build=<entier>`,
  sans session, comptée par origine comme les routes invité (ADR-0025) :
  l'app l'appelle **avant** toute connexion.

## Ce qu'il ne possède pas

- Les seuils eux-mêmes : ce sont des faits d'exploitation (quel build est
  publié, lequel est le plus ancien encore supporté), donc des variables
  d'environnement `APP_RELEASE__{IOS,ANDROID}_{MINIMUM_BUILD,LATEST_BUILD,STORE_URL}`
  (voir `docs/DEPLOIEMENT.md`). Les valeurs par défaut (`0`, `0`, vide)
  répondent toujours `ok` : un déploiement sans ces clés ne bloque personne.
- Les comptes, les appareils (`devices` sait quelle version chaque
  installation a vue ; ce module ne regarde que la requête).

## Les choix qu'on ne devine pas

- `build` est un **entier** (le `buildNumber`/`versionCode` natif), jamais une
  chaîne `1.2.3` : comparable sans ambiguïté, et c'est ce que l'app connaît
  de façon sûre.
- `minimum_build` > `latest_build` est une erreur de configuration ; la règle
  prend alors `latest = minimum` plutôt que d'annoncer une mise à jour vers un
  build qui n'existe pas.
- Pas de module `health` pour cette route : les sondes sont hors préfixe
  d'API et s'adressent à l'orchestrateur ; celle-ci s'adresse à l'app et suit
  le versionnement de l'API.
