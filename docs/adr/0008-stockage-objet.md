# ADR-0008 — Stockage objet : un port, un adaptateur S3, un registre de buckets

**Statut :** Acceptée — 2026-10-20, reprise du cadre `zoumani_api`

## Contexte

Le premier besoin concret est la photo de profil, mais la question est plus
large : CV importés, CV et lettres générés en PDF, photos améliorées,
modèles de CV — tout cela arrive, et la V1 le servait déjà. Il fallait décider **une fois** comment
un fichier est stocké, référencé et servi.

L'expérience de la V1 `karria_api`, en production sur Scaleway depuis avril 2026,
fournit une liste de défauts mesurés plutôt que des hypothèses : URLs
complètes persistées en base, e-mail de l'utilisateur dans le chemin de
l'objet, `boto3` synchrone enveloppé dans `asyncio.to_thread`, client
conservé dans deux attributs de classe, un seul bucket pour tous les usages
et tous les environnements, aucune limite de taille ni de type, aucun envoi
direct navigateur → stockage.

Deux exigences encadraient la décision : brancher Scaleway aujourd'hui,
pouvoir en partir demain sans toucher au code métier ; et n'avoir plus
qu'un nom de bucket à déclarer pour ouvrir un nouvel usage.

## Décision

**1. Le stockage objet est une capacité transverse, dans `shared/storage/`.**
Il ne porte aucune règle métier et plusieurs modules l'utilisent. En faire
un module métier serait une faute : le contrat d'indépendance interdirait
aux autres de l'importer.

**2. Un port `ObjectStorage`, un unique adaptateur S3.** Le protocole S3
est le même chez tous les fournisseurs ; seules changent l'adresse et
quelques bornes. Ces particularités sont décrites **en données** dans un
catalogue (`shared/config/providers.py`). Aucun aiguillage par fournisseur
n'existe hors de la fabrique.

**3. En base on stocke la clé de l'objet, jamais une URL.** L'API rend une
URL signée, calculée à la lecture par le presenter du module. Inscrit comme
interdit n°6.12 dans `AGENTS.md`.

**4. Un registre de buckets déclaratif.** Une valeur dans `Bucket`, une
entrée dans `REGISTRY` avec sa politique, `make storage-provision`. Le nom
physique est dérivé : `<préfixe>-<environnement>-<usage>`.

**5. Les clés sont opaques et sans donnée personnelle**, construites par
`build_key()`. Le nom d'origine d'un fichier est restitué par
`Content-Disposition` à la présignature, jamais par le chemin.

**6. `aioboto3`**, et le client reste ouvert pour toute la vie du processus,
tenu par une `AsyncExitStack` du `lifespan`.

**7. Le domaine n'a pas accès au stockage** — contrat `import-linter`.

## Justification

- **La bascule de fournisseur est vérifiée, pas promise.** Les tests
  d'intégration s'exécutent sur MinIO avec le code écrit pour Scaleway.
  Aucun d'eux n'a été écrit pour MinIO en particulier : ils exercent
  l'adaptateur générique. C'est ce qui donne sa valeur à la promesse.
- **La clé plutôt que l'URL est ce qui rend la migration possible.** Une
  clé reste valide à l'identique chez le fournisseur suivant ; si des URLs
  étaient stockées, changer de crémerie exigerait une migration de base en
  plus du déplacement des octets.
- **Décrire les fournisseurs en données évite la prolifération des `if`.**
  Chaque `if provider == ...` ajouté ailleurs érode la promesse un peu
  plus, et silencieusement. Un seul point d'aiguillage se surveille.
- **Le port asynchrone est une contrainte rendue visible.** `boto3`
  synchrone dans `to_thread` occupe un thread du pool pendant tout le
  transfert : le nombre d'envois simultanés se plafonne alors au nombre de
  threads, sans que rien ne le dise.
- **La politique par bucket est vérifiée avant le réseau.** Refuser après
  transmission gaspille la bande passante et fait attendre l'utilisateur
  pour rien.

## Conséquences

**Positives**

- Changer de fournisseur : `STORAGE__PROVIDER` et trois secrets. Le
  déplacement des octets reste un script à part — c'est un travail
  d'exploitation, pas de code.
- Ouvrir un usage : une ligne au registre.
- Les tests unitaires écrivent des fichiers sans MinIO ni conteneur, avec
  un adaptateur en mémoire qui applique **la même** politique.
- Un bucket manquant empêche le conteneur de démarrer ; une panne du
  stockage une fois démarré ne retire pas l'instance du service.

**Négatives, assumées**

- `aioboto3` amène `boto3` et `botocore`, soit une dépendance volumineuse.
  Il n'existe pas de client S3 asynchrone sérieux plus léger.
- Une URL signée expire : le client doit redemander la ressource plutôt que
  de mettre l'URL en cache. C'est le prix de buckets privés.
- L'envoi direct par URL présignée impose deux appels côté client, et un
  contrôle strict de la clé confirmée côté serveur — le serveur ne voyant
  pas passer les octets.

## Alternatives écartées

- **Un module métier `storage`** — le contrat d'indépendance interdirait
  aux autres modules de l'importer.
- **Le SDK propre à Scaleway** — souderait le code au fournisseur, ce que
  toute cette ADR cherche à éviter.
- **Servir les fichiers depuis un bucket public** — supprime la
  présignature, mais rend tout objet lisible de quiconque devine son nom.
  Réservé, le cas échéant, à des ressources sans aucun caractère personnel.
- **Stocker les fichiers en base (`bytea`)** — gonfle les sauvegardes et la
  mémoire, sans aucun bénéfice.
