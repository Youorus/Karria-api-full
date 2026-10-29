# ADR-0023 — Encaissements : un module `payments` sur Stripe Checkout, sans table ni mode démonstration

**Statut :** Proposée — 2026-10-29 (régularise le commit `5b9b71c` du 2026-10-26, livré sans ADR ; à valider par Marc)
**Complète :** ADR-0007 (un seul type de compte), ADR-0020 (modèles de CV, `is_premium`)
**Contexte :** module `payments`, carte « Offrir un café » et modèles Premium de l'app

## Le problème

L'app propose d'offrir un café à l'équipe (1 à 3 tasses, ou un montant libre) et
vendra des modèles de CV Premium. Le commit `5b9b71c` a mis en production un
module `payments` (Stripe Checkout, SDK `stripe`) qui, relu le 2026-10-29 :

- ouvrait des sessions de paiement **sans authentification**, sans jamais
  rattacher la personne (`user_id` toujours vide) ;
- acceptait n'importe quelle `return_url` : la page Stripe aux couleurs de
  Karria redirigeait où l'appelant voulait (relais d'hameçonnage) ;
- sans clé Stripe (le cas de la production), rendait une fausse adresse de
  paiement et un statut **« payé »** pour tout identifiant `demo_…` — un
  client qui s'y fierait débloquerait un modèle gratuitement ;
- lisait sa configuration par `os.getenv`, levait `HTTPException` dans la
  route, cassait `make check` (lint, mypy), n'avait ni test ni README.

## Décision

1. **Un module `payments`** (AGENTS § 6.5 : un contexte, pas une table) avec les
   quatre couches : la règle de prix et de retour dans `domain/`, le port
   `PaymentGateway` dans `application/`, `StripeGateway` (SDK officiel,
   `StripeClient` par processus, appels dans un fil) dans `infrastructure/`.
2. **Aucune table.** Stripe est la vérité du paiement ; l'app relit l'état par
   `GET /payments/status/{id}`. Le jour où un paiement doit produire un effet
   (un modèle acquis), `HandleWebhook` publiera un événement sur le bus et
   `documents` l'écoutera (règle 4).
3. **Les routes d'ouverture et de relecture exigent la session.** La session
   Stripe porte `user_id` en métadonnée ; l'état n'est rendu qu'à cette
   personne, `404` sinon — sans dire si la session existe.
4. **La règle de retour** est du domaine : `https://karria.fr/…`, `www.`, ou
   le schéma de l'app `karria://`. Tout autre → `422 return_url_not_allowed`.
5. **Les bornes de prix** sont du domaine : un café 5 € (1 à 10), montant libre
   1,50 à 500 €, modèle 4,99 € (prix unique en attendant un catalogue porté
   par `documents`).
6. **Pas de mode démonstration.** Sans `PAYMENTS__SECRET_KEY` : `503
   dependency_failure` (`payments_not_configured`). Un faux « payé » n'est pas
   une commodité de développement, c'est une faille.
7. **Le webhook** vérifie la signature (`PAYMENTS__WEBHOOK_SECRET`, vide →
   tout refusé), journalise, et ne livre encore rien.

## Conséquences

- Variables : `PAYMENTS__SECRET_KEY`, `PAYMENTS__WEBHOOK_SECRET`,
  `PAYMENTS__RETURN_URL` (`.env.example`). Aucune n'est posée en production
  au 2026-10-29 : les routes répondent 503, ce qui est le comportement voulu
  tant que Marc n'a pas tranché le point suivant.
- **À trancher par Marc — règles des magasins.** Un paiement web (Stripe) pour
  un contenu numérique consommé dans l'app iOS relève de la règle 3.1.1 de
  l'App Store (achat intégré obligatoire) ; Google Play a l'équivalent. Un
  pourboire (« café ») est toléré par Apple s'il n'ouvre **aucune**
  fonctionnalité. L'achat de modèles par Stripe depuis l'app risque le refus
  en revue ; l'app prévoyait d'ailleurs RevenueCat (`karria_app/docs/REPRISE.md`).
- La dépendance `stripe` est entrée sans accord (AGENTS § 6.11) ; elle est
  gardée parce que le module la justifie, et consignée ici.

## Alternatives écartées

- **Retirer le module** jusqu'à la décision : la carte « café » de l'app en
  cours de travail l'appelle déjà ; un 503 explicite vaut mieux qu'un 404.
- **Une table des paiements** : rien à relire tant qu'aucun effet n'est produit
  côté Karria ; elle viendra avec le premier événement livré.
