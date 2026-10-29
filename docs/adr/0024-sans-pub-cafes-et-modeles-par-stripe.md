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
