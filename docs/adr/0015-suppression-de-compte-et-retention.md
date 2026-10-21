# ADR-0015 — Suppression de compte : effacement physique, trace de départ anonyme, oubli par événement

**Statut :** Acceptée — 2026-10-21
**Complète :** ADR-0010 (sessions et comptes), ADR-0012 (appareils), ADR-0014 (CV sans clé étrangère)

## Contexte

Le contrat de l'app (`karria_app/docs/contrat-api-compte.md`,
`auth.api.ts : supprimerMonCompte`) fixe `DELETE /auth/me` avec un corps
`{ reason, details }` et une réponse `204`. Le RGPD fixe le reste : une
personne qui demande l'effacement de ses données doit l'obtenir, partout,
sans délai raisonnable à justifier.

Trois contraintes d'architecture rendent la question non triviale :

1. **le compte est dispersé par construction.** `identity` possède le
   compte, `devices` les appareils, `cv` le CV et les photos. Aucun ne lit
   la table d'un autre (règle 4), et `cv_*` comme `devices_*` référencent
   `user_id` **sans clé étrangère** (ADR-0012, ADR-0014) : `ON DELETE
   CASCADE` ne peut rien pour eux ;
2. **des fichiers vivent hors de la base** (photos dans le bucket `media`,
   ADR-0008) : une transaction PostgreSQL ne les couvre pas ;
3. **l'équipe produit a besoin de savoir pourquoi les gens partent**, sans
   que cela recrée une fiche sur quelqu'un qui a demandé à disparaître.

La V1 n'avait pas de suppression de compte servie à l'app.

## Décision

### 1. L'effacement est physique, et il est transactionnel chez `identity`

`DeleteAccount` efface — **ne marque pas** — sessions (ouvertes et closes),
rattachements Apple/Google, parcours de connexion de l'adresse et la ligne
du compte, et écrit la trace de départ, **dans une seule transaction**. À la
validation, la personne n'a plus de session, son adresse n'existe plus,
son `sub` Apple ou Google n'est plus connu. Rien d'intermédiaire n'est
observable : pas d'état « en cours de suppression », pas de compte
« désactivé ».

Conséquence voulue : **la même adresse peut se réinscrire**, et c'est une
première venue (`created_account: true`, nouvel identifiant). Un compte
supprimé n'a pas de mémoire.

Les clés étrangères des sessions et des rattachements sont en `CASCADE` : un
filet, pas le mécanisme. Le use case efface explicitement, pour que
l'intention se lise sans connaître le schéma et pour que les doublures de
test n'aient pas à simuler une cascade.

### 2. Une trace anonyme survit, et rien d'autre

`identity_departure_feedback` garde la **raison** (six codes du contrat), le
**mot libre** (≤ 1 000 caractères, blancs ôtés, `null` si vide), **l'âge du
compte en jours**, la **plateforme** et la **version de l'app** de l'appareil
principal si un appareil était déclaré, et la date. **Ni `user_id`, ni
adresse, ni identifiant d'installation** — la table n'a aucune colonne qui
désigne quelqu'un, et un test d'intégration le vérifie sur le schéma.

Pourquoi si peu : modèle d'appareil + version d'OS + pays suffisent à
reconnaître une personne dans un petit effectif. Deux colonnes grossières
(`ios`/`android`, version de l'app) renseignent le produit sans recomposer
une fiche.

Le mot libre est **expurgé** de ce qui ressemble à une adresse e-mail ou à
un numéro de téléphone (`[e-mail]`, `[téléphone]`) avant d'être retenu —
c'est ce que le contrat de l'app promet. Ce que la trace ne garantit
**pas** : un nom écrit en toutes lettres y reste ; le reconnaître sans
faux positifs massifs n'est pas possible. Le texte est donc traité comme
potentiellement personnel — pas d'export, lecture réservée à l'analyse
produit — et sa rétention relève d'une décision à venir (`PLUS-TARD.md`).

La requête est validée par le **domaine**, pas par Pydantic, pour rendre
les raisons fines du contrat sous le champ fautif :
`account_deletion_reason_invalid`, `account_deletion_details_invalid`,
`account_deletion_details_too_long` (avec `max_length`). Même choix que
pour l'adresse à la connexion (`user_email_invalid`).

### 3. Les autres modules oublient par événement, chacun pour soi

`identity` publie `AccountDeleted(user_id)` **après** la validation de sa
transaction — jamais avant : un abonné qui efface un CV ne doit pas le
faire pour un compte que la transaction aurait finalement gardé.

Chaque module qui référence un `user_id` s'y abonne :

- `devices.ForgetUserDevices` efface les installations du compte, jetons
  compris ;
- `cv.ForgetUser` efface brouillon, CV, photos en attente — lignes **et**
  objets.

Les deux sont **idempotents** : rejouer l'événement pour un compte déjà
oublié n'efface rien et ne lève pas. C'est ce qui rend la reprise possible.

### 4. Les abonnements croisés vivent dans `bootstrap`

Un module ne peut pas s'abonner à l'événement d'un autre : il devrait en
importer le type, ce que la règle 4 interdit. `bootstrap/wiring.py` porte
donc `subscribe_bridges(bus, storage=…)`, appelé par le `lifespan` **une
fois le stockage ouvert** — l'oubli du CV efface des objets. Chaque abonné
reçoit ses adaptateurs assemblés par son propre module (`build_forget_*`) :
`bootstrap` ne connaît pas leurs dépôts.

C'est le même geste que `resolve_principal ← identity.ResolveSession`
(ADR-0007), appliqué au bus. Ce que `ModuleDescriptor.subscribe_events`
offrait — un module s'abonnant à **ses** événements — ne couvre pas ce cas.

### 5. Pour les objets : d'abord les fichiers, ensuite les lignes

`cv.ForgetUser` supprime chaque objet du bucket, **puis** efface dans une
transaction les lignes dont l'objet a bien disparu. Une clé qui résiste
garde sa ligne : la ligne porte la clé, donc un rejeu la retrouve et
réessaie. L'échec est journalisé au niveau erreur avec ses clés
(`cv.forget_incomplete`).

C'est l'ordre **inverse** de la finalisation (ADR-0014 : la ligne dans la
transaction, l'objet après). La raison est symétrique : à la finalisation,
un objet orphelin coûte des centimes et un CV perdu coûte une personne ; à
la suppression, une ligne orpheline est une donnée personnelle que l'on
sait retrouver, un objet orphelin est une donnée personnelle que **rien ne
désigne plus**.

### 6. La plateforme vient de `devices` par un port, pas par la requête

Le contrat ne fait pas voyager la plateforme dans `DELETE /auth/me`, et
`User-Agent` ne dit rien de fiable. `identity` déclare un port
`MainDeviceLookup` (« sur quoi tournait l'app de cette personne ? »), avec
une implémentation par défaut qui répond « inconnu » ; `bootstrap/wiring.py`
le branche sur `devices.GetMainDevice`. Lu **avant** la transaction, pendant
que l'appareil existe encore. Un branchement oublié appauvrit la trace, il
ne casse rien.

## Justification

- **Physique plutôt que logique** : un compte « désactivé » reste une
  adresse en base, donc une donnée à protéger, à sauvegarder, à oublier plus
  tard — c'est-à-dire jamais. Le RGPD demande l'effacement ; on efface.
- **Une transaction chez `identity`, des transactions séparées ailleurs** :
  c'est la seule atomicité qu'un monolithe modulaire sans clés étrangères
  croisées peut offrir sans violer ses frontières. Ce qui compte est
  garanti : à la réponse `204`, la personne ne peut plus rien faire et son
  adresse est libre.
- **Idempotence plutôt que durabilité du bus** : le bus est in-process et
  non durable (voir `shared/events/bus.py`). Plutôt qu'introduire un outbox
  aujourd'hui, on fait en sorte que **rejouer** soit toujours sûr, et on
  journalise ce qui a manqué.
- **La trace est une statistique, pas une fiche** : c'est ce qui permet de
  la garder alors que tout le reste part.

## Conséquences

- Cinq tables touchées à la suppression (`identity_*`), deux chez
  `devices`, trois chez `cv`, plus le bucket `media`. Une table nouvelle :
  `identity_departure_feedback` (migration `2_…_suppression_de_compte`).
- `identity_login_challenges` perd sa promesse « survit à la suppression
  pour le support » : les parcours portent l'adresse en clair, ils partent
  avec le compte.
- Le journal `identity.account_deleted` porte le `user_id` et les compteurs,
  **pas la raison** : la raison ne regarde que la trace anonyme. C'est la
  seule trace reliant un identifiant à un départ, et elle sert au support
  (« j'ai supprimé par erreur » — réponse : c'est irréversible).
- **Non durable** : si le processus tombe entre la validation de la
  transaction `identity` et la publication de l'événement, `devices` et
  `cv` gardent des lignes orphelines (`user_id` sans compte). Le compte
  lui-même est bien parti. Une commande de balayage qui rejoue l'oubli
  pour les `user_id` sans compte est à écrire (`PLUS-TARD.md`) ; les cas
  d'usage sont déjà idempotents pour la recevoir.
- Les tickets Expo en attente de reçu (`devices_push_tickets`) ne portent
  pas de `user_id` et ne sont pas touchés : ils se purgent avec la commande
  planifiée (ADR-0012).

## Alternatives écartées

- **Suppression logique (`deleted_at`)** — garde l'adresse, donc la donnée ;
  oblige chaque lecture à filtrer ; et la réinscription devrait
  « réactiver » un compte dont la personne a demandé l'oubli.
- **`ON DELETE CASCADE` jusqu'aux tables de `cv` et `devices`** — exigerait
  des clés étrangères entre modules, que l'ADR-0012 et l'ADR-0014 ont
  refusées pour que les modules restent extractibles.
- **Outbox transactionnel pour `AccountDeleted`** — la bonne réponse à la
  non-durabilité du bus, mais une pièce d'infrastructure entière (table,
  relais, idempotence des consommateurs) pour un événement aujourd'hui.
  L'idempotence des consommateurs est déjà là ; le relais viendra avec le
  deuxième événement qui doit absolument aboutir.
- **Lignes d'abord, objets ensuite pour le CV** (l'ordre de la
  finalisation) — un objet raté ne serait plus désigné par rien ; il
  faudrait lister le bucket par préfixe `users/{id}/` pour le retrouver,
  ce qui marche tant que les clés gardent cette forme, et seulement là.
- **Faire voyager `platform`/`app_version` dans le corps de la requête** —
  s'écarte du contrat fixé avec l'app, et une déclaration du client vaut
  moins qu'une fiche déjà connue du serveur.
- **Garder un `user_id` haché dans la trace « pour dédoublonner »** — un
  condensat d'identifiant reste un identifiant ; la trace cesserait d'être
  anonyme.
