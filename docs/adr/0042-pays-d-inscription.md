# ADR-0042 — Le pays d'inscription : la région du téléphone, notée une fois sur le compte

**Statut :** Acceptée — 2026-11-20 (décision de Marc : « savoir d'où viennent mes utilisateurs sans le leur demander », « l'afficher dans l'alerte d'inscription »). Le détail du contrat (noms des champs, valeur hors forme ignorée) reste à relire.
**Complète :** ADR-0010 (un compte est une adresse prouvée), ADR-0012 (appareils), ADR-0015 (suppression de compte)
**Contexte :** module `identity` (`domain/accounts.py`, `identity_users`, migration 29), module `devices` (`DeviceDeclared`), `bootstrap/wiring.py`, `shared/countries.py`, `scripts/signups.py`

## Le problème

Pour décider d'une expansion, il faut savoir de quels pays viennent les
inscrits. Rien de durable ne le disait : l'app annonce bien `locale`,
`timezone` et `region` dans `PUT /devices/{installation_id}`, mais la fiche
de l'appareil est **supprimée à la déconnexion** ; et la colonne `country`
(en-tête `CF-IPCountry`) reste vide, le serveur n'étant pas derrière
Cloudflare. Le demander à l'inscription est exclu : l'inscription ne demande
rien (contrat de l'app).

## La décision

**Le compte retient, une seule fois, la région et le fuseau horaire du
téléphone** : `identity_users.signup_region` (code ISO 3166-1 alpha-2, le
réglage « Région » de l'appareil) et `signup_timezone` (nom IANA). L'app les
lit sans permission et les joint aux requêtes qui peuvent créer un compte.

- **Le contrat.** Deux champs facultatifs, `region` et `timezone` — les
  mêmes noms que dans `PUT /devices` — sur `POST /auth/email/verify`,
  `POST /auth/social/google` et `POST /auth/social/apple`. Pas sur
  `/auth/email/start` : c'est la validation du code qui crée le compte, et
  l'y joindre aurait exigé deux colonnes de plus sur les parcours.
- **La forme est stricte, le refus n'existe pas.** `region` : exactement deux
  lettres majuscules, `ZZ` exclu ; `timezone` : forme IANA, 64 caractères au
  plus. Aucune réparation (ni majuscules, ni blancs ôtés). Une valeur hors
  forme, d'un autre type ou absente est **ignorée** — jamais un `422` : une
  statistique ne doit pas coûter une connexion. La règle vit dans le domaine
  (`parse_signup_region`, `parse_signup_timezone`) ; le schéma HTTP l'appelle.
- **Une seule écriture.** À la création du compte, dans la même écriture que
  lui et **avant** `UserRegistered` ; sinon à la première occasion : une
  connexion ultérieure qui les porte, ou le repli ci-dessous. Chaque colonne
  est indépendante. Une valeur enregistrée n'est **jamais** remplacée : la
  condition est dans la requête (`… AND signup_region IS NULL`), et
  `UserRepository.save` n'écrit pas ces colonnes — une connexion qui a lu le
  compte avant ne peut pas les remettre à vide.
- **Le repli, pour les versions de l'app déjà installées.** Elles n'envoient
  rien à la connexion, mais déclarent l'appareil juste après. `devices`
  publie `DeviceDeclared(user_id, region, timezone)` à chaque déclaration qui
  porte l'une des deux valeurs ; `bootstrap/wiring.py` y abonne
  `identity.RecordSignupOrigin`, qui applique la même validation et ne
  complète que ce qui manque. Aucun module n'importe l'autre. Coût : une
  lecture du compte par démarrage de l'app, une écriture une seule fois.
- **L'alerte d'inscription** dit le pays (nom français d'après le code,
  « pays inconnu » sinon) et le fuseau, dans l'objet et dans le corps. Elle
  n'est abonnée qu'à `UserRegistered` : une région connue après coup par le
  repli **ne renvoie pas de courriel**.
- **La lecture.** `uv run python -m scripts.signups [--since AAAA-MM]` :
  inscrits par pays et par mois, lecture seule, aucune adresse affichée.
- **Les noms de pays** sont une table figée (`shared/countries.py`, noms
  français du CLDR) : pas de dépendance pour deux cent cinquante chaînes.

## Ce que cette donnée est, et n'est pas

- **Un réglage d'appareil, pas un lieu.** Une personne à Lyon dont le
  téléphone est réglé sur « Sénégal » compte pour le Sénégal. C'est voulu :
  on mesure d'où viennent les gens, pas où ils se trouvent.
- **Jamais une adresse IP**, jamais une géolocalisation, aucune permission.
- **Une statistique, pas une règle** : elle ne décide d'aucun droit, d'aucun
  prix, d'aucun contenu. Elle ne sort pas par l'API (`GET /auth/me` ne la
  rend pas) et ne se corrige pas.
- **Elle part avec le compte** : ce sont deux colonnes de `identity_users`,
  dont la ligne est effacée physiquement à la suppression (ADR-0015). La
  trace anonyme des départs ne les reprend pas.

## Ce qu'on n'a pas fait, et pourquoi

- **Lire le pays dans l'adresse IP.** Il faudrait une base de géolocalisation
  ou un mandataire qui le dise ; et l'adresse IP est exactement ce que ce
  projet ne conserve pas.
- **Rendre la région modifiable ou la rafraîchir.** Ce serait le pays
  « actuel », une autre question — et une donnée qui bouge se lit mal par
  mois d'inscription.
- **Refuser (`422`) une région mal formée.** Cela bloquerait la connexion
  d'un téléphone dont le moteur annonce une forme inattendue.

## Incertitudes

- **Ce que l'app lit réellement.** Sans `expo-localization`, la région est
  tirée de la langue résolue par `Intl` (`fr-BE` → `BE`). Sur iOS, elle suit
  le réglage « Région » ; sur Android, la région de la langue préférée. Si le
  moteur ne rend que `fr`, rien n'est envoyé. **Non vérifié sur appareil.**
- **La part d'« inconnu »** restera non nulle : comptes antérieurs à la
  migration 29 qui ne rouvrent pas l'app.
- **Confidentialité.** La politique publiée doit mentionner cette donnée
  (voir `docs/REPRISE.md`, session du 2026-11-20 « pays d'inscription ») ;
  la version acceptée n'a pas été incrémentée.
