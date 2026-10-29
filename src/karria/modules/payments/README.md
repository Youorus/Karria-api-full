# Module `payments` — encaissements Stripe : « sans pub », cafés, modèles Premium

Décisions : ADR-0023 (socle Stripe Checkout, retour borné, pas de démonstration)
et ADR-0024 (produits, tables, webhook idempotent, grâce, interrupteur). Contrat
côté app : `karria_app/docs/contrat-api-paiements.md`. Mise en service chez
Stripe : `docs/DEPLOIEMENT.md` § Encaissements.

## Ce qu'il possède

- Le **catalogue et ses prix** (`domain/catalog.py`) : « Karria sans pub »
  4,99 €/mois sans engagement ; les cafés 1 / 2 / 3 / 5 à 2,99 / 4,99 / 7,99 /
  12,99 € ; le modèle Premium au prix que `documents` lui donne. Chaque prix
  a une `lookup_key` stable ; Stripe le porte, le code le crée s'il manque.
- La **projection de l'abonnement** (`payments_accounts`, une ligne par compte :
  client Stripe, statut, fin de période, résiliation programmée, départ de la
  grâce), les **achats confirmés** (`payments_purchases`, uniques par session
  Stripe) et le **registre des notifications** traitées (`payments_webhook_events`).
- Les règles qui ne sont pas chez Stripe : la **grâce de 7 jours** après un
  prélèvement en échec, **un seul abonnement vivant** par personne, une
  nouvelle tardive d'un abonnement remplacé qui n'éteint pas le courant, un
  achat compté **une fois** par session, la **règle de retour** (karria.fr ou
  `karria://`).
- Les routes `/api/v1/payments` : `GET /catalog`, `POST /checkout-session`,
  `GET /status/{id}`, `GET /me`, `POST /portal-session` (session exigée) ;
  `GET /return` (page publique où Stripe dépose la personne, qui la renvoie
  vers la destination revalidée) ; `POST /webhook` (signature Stripe).

## Ce qu'il ne possède pas, et où ça vit

- Le compte et son adresse : `identity`, lu par le port `AccountDirectory`
  (branché dans `bootstrap/wiring.py`). L'adresse ne sert qu'aux reçus Stripe.
- Les modèles de CV, leur prix et leur possession : `documents`, par le port
  `TemplateStore` (vitrine `TemplateShop`). Un modèle payé est remis par
  `grant`, **dans la transaction du webhook**, jamais par le bus.
- L'état réel d'un abonnement : **Stripe fait foi**. La projection est relue
  chez Stripe à chaque notification, jamais déduite de son corps.
- Les reçus et factures : Stripe les émet et les conserve ; Karria n'en garde
  aucune copie nominative.
- La décision « montrer ou non des publicités » : l'app la prend sur
  `ad_free.active` de `GET /payments/me`.

## Les choix qu'on ne devine pas

- **Pas de mode démonstration.** Sans `PAYMENTS__SECRET_KEY` : routes 503,
  catalogue et `/me` avec `enabled: false`, et `/me` ne lit pas la base — ce
  code se déploie donc avant sa migration.
- **`fulfilled`, pas `paid`.** `GET /status/{id}` rend les deux ; l'app attend
  `fulfilled` : c'est la notification signée qui livre, pas le retour du
  navigateur, qui ne prouve rien.
- **Interrupteur par plateforme** (`PAYMENTS__IOS_ENABLED`,
  `PAYMENTS__ANDROID_ENABLED`) : éteindre cache tout paiement dans l'app sans
  livraison. Un fait d'exploitation, exposé dans `enabled`.
- **Suppression de compte** : le client Stripe est supprimé (l'abonnement
  s'arrête), la projection et les achats sont effacés ; le registre des
  notifications, sans donnée personnelle, reste.
- **Un `StripeClient` par processus**, jamais `stripe.api_key` global ; appels
  dans un fil ; les identifiants de prix résolus sont gardés en mémoire.
- **Aucun SDK Stripe dans l'app** : Checkout s'ouvre dans le navigateur
  (session d'authentification), le retour passe par `GET /return`.
