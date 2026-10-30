# Module `identity` — comptes et authentification

Le contexte du **« qui es-tu, et prouve-le »**. Il sert le contrat
d'authentification de `karria_app` (`src/features/auth/`) à l'identique :
`/auth/email/start|resend|verify`, `/auth/social/google|apple`,
`/auth/refresh`, `/auth/logout`, `/auth/me`, `DELETE /auth/me`.

## Ce qu'il possède

- **Le compte** (`identity_users`) : une adresse e-mail prouvée, unique et
  normalisée, le consentement donné « en continuant » (versions +
  horodatage), la dernière connexion. Rien d'autre — ni nom, ni téléphone
  (ADR-0010). Un seul type d'utilisateur, aucune permission.
- **Les rattachements Apple / Google** (`identity_provider_identities`) :
  `(provider, sub)` unique. C'est le `sub` qui reconnaît une personne qui
  revient, jamais l'adresse (ADR-0011).
- **Les sessions** (`identity_sessions`) : condensats des jetons, rotation
  du jeton de rafraîchissement, détection de réutilisation, raison de
  clôture. Jamais un jeton en clair.
- **Les parcours de connexion** (`identity_login_challenges`) : adresse,
  condensat du code, essais, envois, consentement en attente.
- **La suppression du compte** (`DELETE /auth/me`, ADR-0015) : efface
  **physiquement** sessions, rattachements, parcours de l'adresse et compte
  en une transaction, puis publie `AccountDeleted(user_id)` pour que les
  autres modules oublient la personne. La même adresse peut se réinscrire :
  c'est une première venue.
- **La trace des départs** (`identity_departure_feedback`) : raison (six
  codes du contrat), mot libre (≤ 1 000 caractères, expurgé des adresses
  et numéros de téléphone), âge du compte en jours, plateforme et version
  de l'app de l'appareil principal, date.
  **Aucune colonne ne désigne une personne** — ni `user_id`, ni adresse.
  Écriture seule depuis l'API ; la lecture appartient à l'analyse produit.
- **Le consentement à l'IA** (`identity_ai_consents`, ADR-0025) : une ligne
  par compte — la version de l'information acceptée, les fournisseurs et
  usages nommés, la date, le retrait. La version se **calcule** depuis le
  routage réel des modèles (`domain/ai_consent.py`, port `AiProviderCatalog`
  lu dans `Settings.llm`) : un fournisseur de plus redemande l'accord.
  Routes `GET/PUT/DELETE /auth/me/ai-consent` et champ `ai_consent` de
  `GET /auth/me`. La porte que les routes d'IA franchissent vit dans
  `shared/auth` (`AiConsentedPrincipalDep`) ; `bootstrap/wiring.py` y
  branche `EnsureAiConsent`.
- **Les raisons d'erreur** `auth_*`, `user_email_invalid`,
  `account_deletion_*`, `ai_consent_required` et
  `ai_consent_version_mismatch` que l'app branche (`domain/errors.py`).
- **La couture d'authentification** : `ResolveSession`, que
  `bootstrap/wiring.py` branche sur `resolve_principal`. Toute route
  protégée de l'application en dépend ; aucune ne l'importe.

## Ce qu'il ne possède pas, et où ça vit

- **L'envoi d'e-mail** : `shared/mail` (gabarit `login_code`). Ici, un
  port `LoginCodeSender` et un adaptateur de dix lignes.
- **Le `Principal` et les dépendances `CurrentPrincipalDep`** :
  `shared/auth` (ADR-0007). Le module fournit l'implémentation, pas le
  contrat.
- **Les appareils et le détachement à la déconnexion** : module `devices`.
  `Logout` ne touche qu'à la session. À la **suppression**, en revanche,
  `devices` et `cv` réagissent à `AccountDeleted` — abonnés par
  `bootstrap/wiring.py`, jamais appelés d'ici.
- **L'appareil principal** d'une personne qui part : `devices`. Ici, un
  port `MainDeviceLookup` dont l'implémentation par défaut répond
  « inconnu » ; `bootstrap/wiring.py` y branche la vraie réponse.
- **Les permissions d'administration** : n'existent pas. Le jour venu,
  elles arriveront par un port (`PermissionCatalog`), jamais par un champ
  sur le compte.
- **La purge** des sessions closes et des parcours périmés : les dépôts
  l'offrent (`purge_ended_before`, `purge_before`), aucune commande ne
  l'appelle encore (`docs/PLUS-TARD.md`).

## Les choix qu'on ne devine pas en lisant le code

- **La réponse de `/auth/email/start` est la même pour une adresse connue
  ou non**, et un code part dans les deux cas : la validation crée le
  compte. Cette route n'est donc pas un annuaire.
- **Le rejeu d'un jeton de rafraîchissement dans la minute est refusé sans
  fermer les sessions** ; au-delà, tout tombe. Un test qui rejoue « tout
  de suite » teste le doublon, pas le vol.
- **Un parcours inconnu et un parcours périmé rendent la même erreur**
  (410 `auth_login_challenge_expired`). Un identifiant leurre n'apprend
  rien.
- **Le limiteur de tentatives est en mémoire, par processus.** Compté par
  cible **et** par origine (`X-Forwarded-For` ou adresse du client, jamais
  conservée). Deux répliques accorderaient chacune leur quota.
- **Le cache des clés publiques (JWKS) est par processus**, une heure, avec
  relecture forcée sur `kid` inconnu.
- **La langue du code e-mail** vient de `Accept-Language` : c'est le seul
  indice pour une adresse dont aucun compte ne dépend encore.
- **`SECURITY__TOKEN_PEPPER`** poivre les condensats de jetons **et** de
  codes. Le changer déconnecte tout le monde.
- **La suppression efface, elle ne marque pas.** Pas de `deleted_at`, pas
  de compte « désactivé » : à la réponse `204`, il ne reste rien chez
  `identity`, et le journal `identity.account_deleted` est la seule trace
  reliant l'identifiant au départ (sans la raison). Irréversible.
- **Les parcours de connexion partent avec le compte** : ils portent
  l'adresse en clair. Le commentaire V1 « survit pour le support » n'est
  plus vrai depuis l'ADR-0015.
- **`AccountDeleted` est publié après la transaction, jamais avant** : un
  abonné ne doit pas effacer un CV pour un compte finalement conservé.
- **Le consentement à l'IA part avec le compte par clé étrangère en
  cascade** — la seule table du module qui s'y fie : rien n'y doit survivre
  au compte. Le retrait, lui, ne supprime pas la ligne : `revoked_at`
  garde la trace, parce que les deux dates sont la preuve.
- **Une acceptation identique et valable n'est pas réécrite** : sa date
  d'origine est ce qui en fait une preuve (même règle que le consentement
  aux conditions).
