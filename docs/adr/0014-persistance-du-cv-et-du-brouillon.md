# ADR-0014 — CV et brouillon en documents JSONB, un par personne, photo retenue par identifiant

**Statut :** Acceptée — 2026-10-21
**Contexte :** module `cv`, tables `cv_drafts`, `cv_curricula`, `cv_photos`

## Le problème

Le CV doit être stocké pour que la génération de documents (V1
`generated_cv`, `cv_template`, `pdf_engine`) le consomme demain avec les
**mêmes sémantiques** que la V1 : `summary`, `image_url`, `experiences`,
`educations`, `skills`, `languages`, `interests`, `projects`, plus
`identity` et `contact` que le contrat a déplacés dans le CV. Le brouillon
doit survivre à la fermeture de l'application et se reprendre sur un autre
appareil.

La V1 rangeait le CV dans `cvs` et **six tables de sections**, chacune avec
sa clé étrangère, son CRUD et sa migration. Aucune requête n'a jamais
filtré une expérience par entreprise : le CV se lit entier, s'écrit entier.
La génération, elle, rattache ses sorties aux éléments d'origine par
identifiant (`source_reconciler`) : les éléments doivent avoir un `id`
stable — c'est la seule exigence structurelle que la V1 impose.

Le contrat, enfin, fait circuler la photo retenue sous la forme d'une
`image_url`. Or l'URL rendue est signée et expire, et le socle interdit de
stocker une URL (ADR-0008) : le serveur ne peut ni la persister, ni la
reparser pour retrouver la clé — c'est exactement l'`extract_object_key`
que la V1 a dû écrire.

## Ce qu'on décide

### 1. Trois tables, des sections en JSONB

- `cv_drafts` — un brouillon par `user_id` (unique) ; `chapters` est un
  document JSONB `{clé: {status, data, items}}`. Le chapitre courant se
  **déduit** de l'ordre fixe et des statuts : le stocker créerait un second
  état à garder cohérent avec le premier.
- `cv_curricula` — un CV par `user_id` (unique) ; `identity`, `contact`
  et chaque section sont des colonnes JSONB ; `summary` en texte,
  `image_key` en clé objet (jamais une URL).
- `cv_photos` — les photos envoyées en attente de choix : `user_id`,
  `image_key`, `enhanced`.

Chaque élément de liste porte un `id` UUID attribué à la validation et
conservé dans le JSON : la génération retrouve ses éléments. Chaque
section est **relue par le schéma du domaine** à chaque chargement : une
ligne corrompue lève dans le dépôt, où l'on sait de quelle table elle
vient.

Pourquoi pas six tables : elles n'achèteraient que la possibilité de
requêter ce qu'on ne requête pas, contre six clés étrangères, six
migrations et six dépôts. Le jour où une requête transversale apparaît
(« qui a travaillé chez Zara ? »), PostgreSQL indexe le JSONB ; et si cela
ne suffit pas, extraire une section vers une table est une migration de
données, pas un changement de contrat.

### 2. `user_id` sans clé étrangère

Les comptes appartiennent à un autre module (règle 4). Le CV référence le
compte par identifiant ; la cohérence passe par le bus — un compte supprimé
publiera un événement auquel ce module réagira en supprimant ses lignes et
ses objets. En retour, la finalisation publie `CvFinalized` avec l'identité
et le contact : le module des comptes s'y abonnera pour « mettre à jour
l'identité du profil », comme le contrat le demande, sans qu'aucun module
n'importe l'autre.

### 3. La photo se retient par identifiant

`POST /cv/photo` écrit la photo (normalisée, ADR-0009) dans le bucket
`media`, enregistre une ligne `cv_photos` et rend `{image_id, image_url,
enhanced}`. Le chapitre `photo` du brouillon reçoit `{image_id}` ; le
serveur vérifie que la photo existe **et appartient à la personne**. À la
lecture, le brouillon et le CV rendent `image_url` signée à partir de la
clé. À la finalisation, la clé retenue passe dans le CV, les autres photos
de la personne sont oubliées — la ligne dans la transaction, l'objet après
(un objet orphelin coûte des centimes, un CV perdu coûte une personne).

C'est un écart avec le contrat tel qu'il était écrit (`{ image_url }`
dans le chapitre `photo`), corrigé dans `karria_app/docs/contrat-api-cv.md`
le même jour : l'alternative — reparser l'URL — est précisément ce que
l'ADR-0008 interdit.

### 4. La finalisation est transactionnelle, l'import ne l'est pas

Écrire le CV, supprimer le brouillon et oublier les photos réussissent ou
échouent ensemble (`UnitOfWork`). Refaire son CV garde l'identifiant du CV
existant : les références extérieures (documents générés, dossiers) restent
valides. L'import, lui, ne touche jamais un chapitre validé et pose le reste
en propositions : il n'y a rien à annuler, puisqu'il n'écrase rien.

## Conséquences

- Ajouter un champ à une section = une ligne dans le schéma Pydantic, sans
  migration ; renommer un champ V1 reste interdit (contrat).
- Les routes d'édition directe de la V1 (`PUT /cv/update`, sous-ressources)
  se brancheront sur le même dépôt : le CV se réécrit entier, ce qui est
  aussi ce que fait l'application.
- Les tables portent le préfixe du module (`cv_*`), comme celles des autres
  modules ; `TABLES_TO_TRUNCATE` les connaît.
- Ce qui n'est pas fait : la réaction à la suppression d'un compte
  (événement à définir par le module des comptes) et l'édition directe
  (`docs/PLUS-TARD.md`).
