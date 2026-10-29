# Module `payments` — encaissements Stripe Checkout

## Ce qu'il possède

- La **règle de prix** (`domain/`) : un café = 5 € (1 à 10), un montant libre
  de 1,50 à 500 €, un modèle de CV Premium à 4,99 €. Les bornes sont du
  domaine, pas de la configuration.
- La **règle de retour** : après le paiement, Stripe ne renvoie que vers
  `https://karria.fr/…` (ou `www.`) ou vers l'app (`karria://…`). Une adresse
  quelconque ferait de la page Stripe aux couleurs de Karria un relais
  d'hameçonnage.
- Trois routes sous `/api/v1/payments` : `POST /checkout-session` et
  `GET /status/{session_id}` **exigent la session** de la personne — la
  session Stripe porte son identifiant en métadonnée, et l'état n'est rendu
  qu'à elle (`404` sinon, sans dire si la session existe) ; `POST /webhook`
  est appelé par Stripe et s'authentifie par sa signature.
- Le port `PaymentGateway` et son adaptateur `StripeGateway` (SDK officiel,
  `StripeClient` par processus, appels dans un fil).

## Ce qu'il ne possède pas, et où ça vit

- **Aucune table.** Stripe est la vérité du paiement ; l'app relit l'état.
- **Aucune livraison** à la réception d'un événement : rien n'est débloqué
  ni envoyé. Le jour où un paiement doit produire un effet (un modèle
  acquis dans `documents`), `HandleWebhook` publiera un événement sur le bus
  et `documents` l'écoutera — jamais un import direct (règle 4).
- Les prix par modèle de CV : `documents` les connaît (`is_premium`) ; ici un
  prix unique en attendant un catalogue.

## Les choix qu'on ne devine pas

- **Pas de mode démonstration.** Sans `PAYMENTS__SECRET_KEY`, les routes
  répondent `503 dependency_failure` (`payments_not_configured`). La
  première version rendait une fausse adresse et un statut « payé » pour
  tout identifiant `demo_…` — un client qui s'y serait fié aurait débloqué
  un modèle gratuitement.
- Configuration : `PAYMENTS__SECRET_KEY`, `PAYMENTS__WEBHOOK_SECRET`,
  `PAYMENTS__RETURN_URL` (défaut `https://karria.fr/paiement`). Secret de
  webhook vide → tout événement est refusé.
- **Achats dans l'app et règles des magasins** : un paiement web (Stripe)
  pour un contenu numérique consommé dans l'app iOS relève de la règle
  3.1.1 de l'App Store (achat intégré obligatoire) ; Google Play a la règle
  équivalente. Le pourboire (« café ») est toléré par Apple s'il n'ouvre
  aucune fonctionnalité. **Décision de Marc** avant d'exposer l'achat de
  modèles par Stripe dans l'app.
