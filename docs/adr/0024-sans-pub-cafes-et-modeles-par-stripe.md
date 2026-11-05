# ADR-0024 — « Karria sans pub », cafés et modèles Premium : Stripe Checkout seul, droits projetés, webhook idempotent

**Statut :** Proposée — 2026-10-29 (à valider par Marc)
**Complète :** ADR-0023 (module `payments` sur Stripe Checkout), ADR-0015 (suppression de compte), ADR-0020 (modèles de CV, `is_premium` / `price`)
**Revient sur :** ADR-0023 § Décision 2 (« aucune table ») et § Décision 5 (café à 5 €, montant libre)
**Contexte :** carte de soutien du Profil, publicité AdMob, modèles Premium de l'app

## Le problème

L'ADR-0023 a assaini un module `payments` sans table ni effet : il ouvrait une
session Stripe et laissait l'app relire « payé ». Marc tranche le point qu'elle
laissait ouvert (règle 3.1.1 de l'App Store) : **Stripe uniquement, par le
navigateur, comme Zoumani, qu'Apple a validé** ; aucun achat intégré, aucun
RevenueCat, et — leçon de Zoumani — aucun SDK Stripe natif dans le binaire
(PassKit déclencherait la question « où est Apple Pay ? »).

Trois produits, validés par Marc :

| Produit | Prix | Effet |
|---|---|---|
| « Karria sans pub » | 4,99 €/mois, renouvelé chaque mois, sans engagement, résiliable depuis le portail Stripe | aucune publicité AdMob ne se charge |
| « Offrir un café » | 1 café 2,99 €, 2 cafés 4,99 €, 3 cafés 7,99 €, 5 cafés 12,99 € | aucun droit ; un merci |
| Modèle de CV Premium | le prix du modèle dans `documents` | le modèle est à la personne |

Un paiement qui produit un effet exige ce que l'ADR-0023 avait reporté : une
vérité locale (qui est sans pub, qui possède quoi), une livraison fiable, et
des notifications traitées une fois.

## Décision

1. **Les prix sont du domaine** (`domain/catalog.py`) : 4,99 €/mois, les
   quatre paliers de cafés, et le prix du modèle lu dans `documents` par le
   port `TemplateStore`. Chez Stripe, chaque prix est **retrouvé par
   `lookup_key`** (`karria_ad_free_monthly`, `karria_coffee_{n}`,
   `karria_template_{uuid}`) et **créé par le code** s'il manque ou si son
   montant diffère — la clé est transférée au nouveau prix. Marc ne saisit
   aucun produit ni prix à la main ; il ne gère que le compte, le portail, le
   webhook et les clés. Prix **TTC** (`tax_behavior=inclusive`) ; Stripe Tax
   facultatif (`PAYMENTS__AUTOMATIC_TAX`).
2. **Trois tables** (migration 12) : `payments_accounts` — une ligne par
   compte, le client Stripe et la **projection** de l'abonnement (statut,
   fin de période, résiliation programmée, départ de la grâce) ;
   `payments_purchases` — les paiements uniques confirmés, uniques par session
   Stripe ; `payments_webhook_events` — le registre des notifications traitées.
   Aucune clé étrangère vers les comptes (ADR-0015).
3. **Stripe fait foi, relu, jamais déduit.** Une notification ne sert qu'à
   désigner ce qu'elle concerne ; l'état est relu (`fetch_subscription`,
   `retrieve_checkout`), parce que Stripe ne garantit pas l'ordre. Une nouvelle
   tardive d'un abonnement remplacé n'éteint pas le nouveau.
4. **Le webhook est idempotent par construction.** L'identifiant d'événement
   est noté dans le registre **dans la même transaction** que l'effet produit :
   un rejeu s'acquitte ; un effet qui échoue annule la note et Stripe réémet
   (503). Événements écoutés : `checkout.session.completed`,
   `customer.subscription.created|updated|deleted`, `invoice.payment_failed`.
5. **Un modèle payé est remis par un port synchrone**, `TemplateStore.grant`
   (← `documents.TemplateShop`, branché dans `bootstrap/wiring.py`), dans la
   transaction du webhook — **pas par le bus**, qui n'est ni durable ni
   rejouable (`shared/events/bus.py` l'interdit pour un paiement). La remise
   est idempotente (`get_or_create` sur (personne, modèle), `source=purchase`).
6. **La grâce est du domaine** : un prélèvement en échec laisse le « sans pub »
   actif **7 jours** après le premier échec constaté (`GRACE_PERIOD`), le temps
   de corriger la carte depuis le portail ; Stripe relance ; s'il résilie,
   l'accès tombe avec lui. L'app lit `ad_free.active` et `until` sur
   `GET /payments/me` et n'initialise pas AdMob quand c'est vrai.
7. **Le retour à l'app passe par une page de l'API** (`GET /payments/return`,
   publique, HTML autonome) : Stripe n'accepte pas `karria://` comme adresse de
   retour. La page revalide la destination (`karria://…` ou
   `https://karria.fr/…`, règle de l'ADR-0023) et y renvoie ; l'app ouvre
   Checkout dans une session d'authentification du navigateur
   (`expo-web-browser`), qui se referme sur ce renvoi, puis **relit le
   serveur** — jamais l'adresse d'arrivée.
8. **L'interrupteur par plateforme** `PAYMENTS__IOS_ENABLED` /
   `PAYMENTS__ANDROID_ENABLED` est exposé (`enabled` du catalogue et de
   `/payments/me`) : éteint, l'app cache tout paiement sans livraison — si
   Apple s'y oppose. Sans clé Stripe, il est éteint d'office. C'est un fait
   d'exploitation (« ce canal est-il ouvert ? »), pas une règle métier.
9. **Supprimer son compte** supprime le client chez Stripe (ce qui résilie
   l'abonnement sur-le-champ) et efface la projection et les achats locaux.
   Stripe conserve reçus et factures (obligation comptable, dix ans) : Karria
   n'en garde aucune copie nominative. Le registre des notifications, sans
   donnée personnelle, reste.
10. **Les cafés n'ont plus de montant libre et valent les paliers de Marc** ; le
    « 5 € l'unité » et le montant libre de l'ADR-0023 sont retirés.

## Conséquences

- Variables : `PAYMENTS__SECRET_KEY`, `PAYMENTS__WEBHOOK_SECRET`,
  `PAYMENTS__RETURN_URL`, `PAYMENTS__IOS_ENABLED`, `PAYMENTS__ANDROID_ENABLED`,
  `PAYMENTS__AUTOMATIC_TAX` ; `APP__PUBLIC_URL` compose la page de retour.
  Sans clé : routes 503, catalogue `enabled: false`, et `GET /payments/me` ne
  lit pas la base — ce qui permet de déployer ce code **avant** sa migration.
- Routes (`/api/v1/payments`) : `GET /catalog`, `POST /checkout-session`,
  `GET /status/{id}` (`fulfilled`), `GET /me`, `POST /portal-session`,
  `GET /return`, `POST /webhook`. Contrat côté app :
  `karria_app/docs/contrat-api-paiements.md`.
- Nouveaux branchements dans `bootstrap/wiring.py` : `get_account_directory`
  (← `identity.GetCurrentUser`, l'adresse des reçus), `get_template_store`
  (← `documents.TemplateShop`), et le pont `AccountDeleted` → `payments`.
- `identity` expose `GetCurrentUser`, `get_current_user_reader`,
  `UserNotFoundError` ; `documents` expose `TemplateShop`, `PremiumTemplate`,
  `get_template_shop`.
- **Pas de réconciliation planifiée** : si un webhook était perdu (point de
  terminaison mal configuré), la projection divergerait de Stripe jusqu'à la
  notification suivante. Noté dans `PLUS-TARD.md` ; le worker n'a donc pas
  besoin des variables `PAYMENTS__*` aujourd'hui.
- Les reçus sont ceux de Stripe (e-mails de reçu à activer dans le tableau de
  bord) ; Karria n'envoie aucun courriel de paiement.

## Alternatives écartées

- **L'achat intégré (StoreKit / Play Billing, RevenueCat)** : décision de Marc,
  cohérente avec Zoumani validée par Apple ; une double intégration coûterait
  deux fois et laisserait 30 % à Apple sur 4,99 €.
- **Le SDK Stripe natif** (`@stripe/stripe-react-native`) : lie PassKit et
  déclenche la question Apple Pay en revue — refus de Zoumani. Checkout dans le
  navigateur suffit ; Apple Pay peut y apparaître côté web sans binaire.
- **Livrer par le bus d'événements** : non durable, au plus une fois ; un
  modèle payé et non remis serait un ticket de support, pas un journal.
- **Déduire l'état de la notification** sans relire Stripe : un `updated`
  ancien reçu après un `deleted` ressusciterait un abonnement terminé.
- **Des prix en ligne (`price_data`) comme Zoumani** : possible, mais le
  portail et les rapports Stripe gagnent à voir des prix nommés ; la clé de
  recherche donne le même « rien à saisir » tout en gardant l'historique.

## 2026-11-05 — le merci d'un paiement : courriel et notification, hors transaction (complète la décision 3 ; revient sur « Karria n'envoie aucun courriel de paiement »)

**Statut :** Proposée — 2026-11-05 (à valider par Marc)

### Le problème

Le 2026-11-05 à 08:15, un vrai café de 2,99 € est encaissé par Stripe. Le
webhook et `GET /status/{id}` répondent 500 (`StripeObject` non itérable,
corrigé en `cfa9e5f`) ; l'app dit « reviens plus tard » et la personne ne
reçoit **rien** : ni courriel, ni notification. Même sans l'incident, elle
n'aurait rien reçu non plus — la décision ci-dessus laissait à Stripe le reçu
et à l'app seule le « merci ». Marc : « mon client doit recevoir des
remerciements par mail et par notification push à la validation de son
paiement. »

### La décision

1. **Chaque livraison vaut un merci**, par courriel **et** par notification :
   un achat enregistré (`_fulfill` : café, modèle) et un abonnement « sans
   pub » qui **naît** (`_sync` : d'aucun abonnement vivant à `ACTIVE`,
   `BillingAccount.activated_from`). Un renouvellement, une carte corrigée
   (`PAST_DUE` → `ACTIVE`) ou une relecture sans changement n'en valent pas :
   Stripe envoie déjà ses reçus.
2. **Hors transaction, jamais bloquant.** Le `ThankYou` naît dans la
   transaction du webhook et est remis à `ThankForPurchase` **après** elle.
   Ce cas d'usage ne lève jamais : un SMTP coupé, Expo injoignable, un compte
   sans adresse se journalisent (`payments.thanks_email_failed`,
   `payments.thanks_push_failed`, `payments.thanks_email_skipped_no_address`)
   et le webhook répond 200 — un rejeu de Stripe pour un courriel
   recompterait un café. Courriel et notification sont indépendants.
3. **Une fois par livraison, par construction** : le rejeu s'arrête au
   registre des événements, le jumeau à `PurchaseRepository.add` (unicité de
   la session), l'abonnement à la transition d'état. Aucune table nouvelle.
4. **Ports et adaptateurs (règle 4), comme `support`** : `payments` déclare
   `UserNotifier` (push), branché dans `bootstrap/wiring.py` sur
   `devices.SendPushToUser` par le même `DevicesUserNotifier` qu'`offers` et
   `support` ; le refus par défaut (`NoUserNotifier`) rend 0 et journalise
   `payments.notifier_unwired`. Le courriel passe par `shared/mail` (socle,
   pas module) derrière le port `ThankYouMailer`, implémenté dans
   `infrastructure/mail.py`. L'adresse vient du port `AccountDirectory` déjà
   branché sur `identity`, le nom du modèle du port `TemplateStore` déjà
   branché sur `documents`.
5. **Trois messages au registre** (`PURCHASE_COFFEE`, `PURCHASE_TEMPLATE`,
   `SUBSCRIPTION_STARTED`), objets **fixes**, contexte réduit à des valeurs
   produites par Karria (montant formaté, nombre de cafés, nom du modèle tel
   que `documents` le vend) — aucun texte ni lien venu de la personne, et le
   rendu échappe tout de toute façon (revues de sécurité de l'ADR-0031).
   Gabarits dans la ligne de l'ADR-0032 (sobres, sombre/clair, logo).
6. **Un type push `purchase`** (ADR-0022, canal `compte`) : sans `id`, l'app
   ouvre le profil (soutien) ; avec `id` (le modèle acheté), le modèle.
   Titres courts, sans emoji ni montant (l'écran verrouillé n'est pas à nous).
7. **Rejeu d'exploitation** : `scripts/payments_thanks.py <purchase_id> --send`
   remet le merci d'un achat par le même chemin, une seule fois (événement
   synthétique `thanks:<purchase_id>` dans `payments_webhook_events`). Écrit
   pour l'achat du 2026-11-05 au matin.

### Alternatives écartées

- **Un événement de domaine `PurchaseFulfilled` consommé par `devices` et par
  un abonné mail** : le bus n'est pas durable et, surtout, il disperse « ce
  qu'on dit après un paiement » dans deux modules qui n'ont pas à connaître
  le café ni le modèle. Le port garde la décision dans `payments`, où elle
  se teste sans base.
- **Envoyer dans la transaction** : un SMTP lent retarderait la réponse à
  Stripe, et un SMTP en panne ferait rejouer un café déjà compté.
- **Une tâche de file (`shared/queue`)** : plus robuste à une panne longue,
  mais le worker n'a pas les variables `PAYMENTS__*` et un merci qui arrive
  avec dix minutes de retard vaut moins qu'un merci immédiat ; une panne
  courte est couverte par le script de rejeu. À revoir si les échecs
  journalisés se multiplient (`PLUS-TARD.md`).
- **Remercier aussi à chaque mensualité** : du bruit ; Stripe envoie la facture.

### Conséquences

- `bootstrap/wiring.py` : un 17e branchement, `payments.get_user_notifier`
  ← `devices.SendPushToUser`, et `build_thank_for_purchase_wired()` pour le
  script. Aucune variable d'environnement nouvelle (SMTP et Expo existent).
- `PurchaseRepository.find(purchase_id)` ; `ThankYou`, `format_amount`,
  `BillingAccount.activated_from` dans le domaine.
- Contrat app : `karria_app/docs/contrat-notifications.md` (type `purchase`)
  et `contrat-api-paiements.md` (§ merci). L'app revérifie d'elle-même un
  achat « en attente » au retour au premier plan et au lancement.
- `PLUS-TARD.md` § « Aucun courriel de Karria autour d'un paiement » est
  caduc pour le merci ; reste vrai pour le prélèvement en échec.
