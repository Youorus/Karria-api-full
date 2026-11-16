# ADR-0039 — Accès de démonstration pour les revues d'Apple et de Google, et paiements coupés dans les apps

**Statut :** Acceptée — 2026-11-16 (Marc : « fais tout le nécessaire pour qu'ils puissent faire leur revue »)
**Complète :** ADR-0010 (connexion par code), ADR-0024 (encaissements Stripe), ADR-0038 (quotas d'IA)
**Contexte :** module `identity` (`domain/review.py`), réglages `REVIEW__*` et `PAYMENTS__*_ENABLED`

## Le problème (audit du 2026-11-16, essais en production)

1. **Aucun accès réutilisable.** Hors Apple et Google, on n'entre qu'avec un
   code à usage unique envoyé par courriel (réutilisé : `410`). Google Play
   exige des identifiants « accessible at all times, reusable, and valid
   regardless of user location » et, si l'app utilise un code à usage unique,
   « provide reusable credentials that bypass that step ». Apple, règle
   2.1(a) : « include demo account info […] if your app includes a login ».
2. **Des paiements Stripe réels dans les apps** (abonnement « sans pub »,
   cafés), activés sur iOS et Android, clé live. Apple 3.1.1 : un abonnement
   qui débloque une fonction passe par l'achat intégré, et hors des
   États-Unis l'app ne peut pas renvoyer vers un autre moyen de paiement
   (3.1.1(a), 3.1.3). Google, règles Paiements : la facturation Play est
   exigée (« an ad-free version of an app » en exemple), renvoyer vers le web
   est interdit hors programme dédié. Et un évaluateur ne paie pas.

## La décision

**Un accès de démonstration étroit.** Une seule adresse (`REVIEW__EMAIL`),
qui n'a pas besoin d'exister : pour elle, le code du parcours est le code fixe
`REVIEW__CODE` (six chiffres, secret d'exploitation) et **aucun courriel ne
part**. Rien d'autre ne change : le parcours expire, le code faux compte comme
un essai (cinq au plus), le frein par origine tient (dix demandes en cinq
minutes), toute autre adresse reçoit son vrai code. Le compte est ordinaire :
il naît à la première connexion, renaît vide après une suppression (un
évaluateur teste la suppression). Vides, les deux réglages n'ouvrent rien.
Un code mal formé est refusé au démarrage.

**Contre la devinette du code fixe** (relevé par la revue de sécurité du
commit) : un frein **global** — toutes origines confondues — de trente codes
faux par heure pour cette adresse (`REVIEW_MAX_FAILURES_PER_HOUR`, par
processus), journalisé (`auth.review_code_rejected`). Six chiffres à trente
essais par heure et par processus : des mois en moyenne pour un compte
fictif. Saturé, le frein refuse aussi le bon code jusqu'à la fin de l'heure :
un déni de service possible pendant une revue, assumé et visible au journal.

Écarté : un compte Google de test (les connexions depuis un lieu inconnu
déclenchent une vérification — identifiants non fiables), un mode
démonstration dans l'app (une seconde app à maintenir, et Apple l'exige
« with prior approval »).

**Les paiements coupés dans les deux apps** (`PAYMENTS__IOS_ENABLED=false`,
`PAYMENTS__ANDROID_ENABLED=false`) : l'interrupteur de l'ADR-0024, sans
livraison. L'app cache alors l'offre, le café et les modèles payants. Les
messages de quota de l'API ne vantent plus « Karria sans pub » : l'app le
propose elle-même, seulement là où l'offre est ouverte. Aucun abonné actif le
jour de la coupure (une projection `none`, un café de test).

Pour rouvrir les paiements dans une app des stores : l'achat intégré d'Apple
et la facturation de Google Play, ou leurs programmes de paiement externe
(Union européenne) — un chantier à part.

## Conséquences

- Réglages : `REVIEW__EMAIL`, `REVIEW__CODE` (secret), dans Dokploy **et**
  `/root/karria-api.env`. Le code se change par configuration.
- Le risque assumé : qui connaît l'adresse et le code entre dans un compte
  fictif, plafonné par les quotas d'IA. On change le code s'il fuit.
- Journal : `auth.review_code_not_sent` à chaque code non envoyé.
