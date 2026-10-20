# ADR-0007 — Principal transverse et permissions déclarées par module

**Statut :** Acceptée — 2026-10-20, reprise du cadre `zoumani_api`

## Contexte

Presque toute l'API de Karria n'a de sens que si l'appelant est identifié :
le fil d'offres est *le sien*, le CV est *le sien*, les quotas sont *les
siens*. S'y ajoutent des surfaces d'administration — consulter un compte,
relancer un matching, examiner un dossier — où un opérateur agit sur le
compte d'autrui.

Il fallait décider **où** vit la notion d'appelant authentifié, et
**comment** s'exprime le droit d'agir, avant d'écrire la première route
protégée. Le mécanisme de session lui-même — comment une session naît,
survit, se prouve et meurt — n'est **pas** tranché ici : il le sera dans
l'ADR du module qui portera les comptes, en respectant le contrat de
l'application (`/auth/email/start`, `/auth/email/verify`, `/auth/refresh`,
jeton d'accès et jeton de rafraîchissement).

La V1 répondait à la question du droit d'agir par un booléen `is_admin`
sur le compte. Conséquence observée : chaque nouvelle capacité
d'administration élargissait le pouvoir de tous les administrateurs à la
fois, sans qu'aucun code ne dise lesquels. Une équipe support qui doit
pouvoir lire un compte se retrouvait capable de le supprimer.

## Décision

**1. `shared/auth/` porte le mécanisme, jamais la politique.** Il fournit :

- `Principal` — appelant authentifié : un `subject_id` et un ensemble
  figé de permissions. Aucun champ métier, aucun rôle nommé ;
- `PrincipalResolver` — port décrivant « transformer une requête entrante
  en `Principal` » ;
- `CurrentPrincipalDep` — dépendance FastAPI qui échoue en
  `AuthenticationError` si l'appelant est anonyme ;
- `require_permissions(...)` — fabrique de dépendance qui échoue en
  `AuthorizationError` si une permission manque.

**2. Les permissions sont des chaînes `<ressource>:<action>` déclarées par
le module qui les fait respecter**, jamais par `shared/`. Chaque module
les expose dans son `ModuleDescriptor` ; `bootstrap/modules.py` en dresse
l'inventaire complet sans qu'aucun module n'ait à en lire un autre.

**3. Il n'existe aucun booléen `is_admin`, ni aujourd'hui ni plus tard.**
Un administrateur est un `Principal` porteur de permissions, rien d'autre.

**4. L'implémentation par défaut du `PrincipalResolver` refuse tout.**
Tant qu'aucun émetteur de jeton n'existe, toute route protégée répond
`401`. Ce refus est délibéré et documenté ; il n'existe pas de mode de
développement qui lise un en-tête `X-User-Id`.

## Justification

- **La permission fine est ce qui permet de donner un pouvoir sans donner
  tous les autres.** `users:read` pour le support, `matching:rerun` pour
  l'exploitation : la granularité est dans le vocabulaire dès le premier
  jour, parce qu'on ne la rétro-ajoute jamais. Rétro-ajouter revient à
  auditer chaque route existante pour deviner qui avait le droit d'y
  accéder.
- **Le mécanisme est transverse, la politique ne l'est pas.** Le fil
  d'offres et le CV auront besoin d'un appelant identifié ; ils n'auront
  jamais besoin de connaître `users:suspend`. Mettre les permissions dans
  `shared/` ferait de `shared/` un catalogue du métier, ce que la règle 1
  interdit.
- **Le refus par défaut est la seule position sûre.** Une porte dérobée de
  développement — un en-tête qui désigne l'appelant — est exactement le
  genre de commodité qui survit jusqu'en production. Le coût de ce choix
  est que les routes protégées sont, pour l'instant, exerçables uniquement
  par surcharge de dépendance en test. C'est un coût de tests, pas un coût
  de sécurité.
- **Le port rend le remplacement indolore.** Jeton opaque en base, JWT
  signé, fournisseur externe : la décision se prendra dans une ADR dédiée
  et ne touchera qu'une implémentation de `PrincipalResolver`, branchée
  dans `bootstrap/wiring.py`.

## Conséquences

**Positives**

- Les routes d'un module sont protégées à l'écriture, pas « après ». Aucune
  route d'administration n'est livrée ouverte.
- L'ajout de l'authentification réelle consiste à fournir une
  implémentation du port et à la câbler : aucune route ne change.
- Les tests exercent la vraie chaîne d'autorisation en substituant le
  `Principal`, comme `health` substitue ses sondes.

**Négatives, assumées**

- **Toute route protégée répond `401` tant que le login n'existe pas.**
  C'est le périmètre décidé : le socle livre le mécanisme, pas les
  sessions.
- L'origine des permissions d'un `Principal` n'est pas encore décidée
  (configuration, table, revendication de jeton). Le port n'en présume
  rien, ce qui laisse la décision entière à l'ADR d'authentification.

## Alternatives écartées

- **Un booléen `is_admin` sur le compte** — l'erreur de la V1. Un seul
  niveau de pouvoir, indivisible, qui grossit à chaque fonctionnalité.
- **Un module technique `auth/` séparé des comptes** — un module est un
  contexte métier, pas une couche technique. Séparer « qui es-tu » de
  « prouve-le » créerait deux modules obligés de se lire l'un l'autre à
  chaque connexion.
- **Une dépendance de développement qui lit `X-User-Id`** — rend les
  routes utilisables tout de suite, au prix d'un contournement
  d'authentification complet dont la présence en production ne serait
  détectée que par un audit.
