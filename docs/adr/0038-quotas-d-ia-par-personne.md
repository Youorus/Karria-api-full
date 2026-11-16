# ADR-0038 — Quotas d'IA par personne, levés par « Karria sans pub »

**Statut :** Acceptée — 2026-11-16 (Marc : « fais les 4 étapes »)
**Complète :** ADR-0018 (couche LLM, coûts mesurés), ADR-0024 (« Karria sans pub »), ADR-0031 (quota d'envois, même forme d'erreur)
**Contexte :** modules `cv`, `applications`, `offers` ; `bootstrap/wiring.py` ; migration 23

## Le problème

Aucune fonction d'IA n'avait de plafond mensuel par personne. Le coût moyen
est faible (≈ 0,09 € par inscrit et par mois aux tarifs 2026, analyse du
2026-11-16), mais il dépend entièrement de quelques personnes : une seule qui
régénère cinquante portraits coûte 3 €, le budget de trente inscrits. Et
`gemini-3.8-flash` double au 1er janvier 2027. Coût par action (tarifs relevés
le 2026-11-16 ; « estimé » = non mesuré) :

| action | coût |
|---|---|
| portrait (`gemini-3.1-flash-image`) | ≈ 5,8 c€ |
| dossier : CV adapté à l'offre | ≈ 1,4 c€ (estimé) |
| import d'un CV | ≈ 1,0 c€ (estimé) |
| lettre écrite | ≈ 0,3 c€ (estimé) |
| message du chat (réponse + mémoire) | ≈ 0,26 c€ (mesuré) |
| explication ATS, chapitre interprété, dictée | ≈ 0,15 c€ |

## La décision

Des plafonds pour le gratuit ; **l'abonnement « Karria sans pub » (4,99 €/mois,
prix inchangé) les lève**. À l'abonné reste un garde-fou d'« usage
raisonnable » que l'usage normal n'atteint pas — un vrai illimité serait un
risque sans plafond, et un « illimité » sans mention n'est pas défendable.

| action | module | gratuit | abonné | fenêtre |
|---|---|---|---|---|
| portrait | `cv` | 3 | 30 | mois civil UTC |
| import de CV | `cv` | 5 | 30 | mois civil UTC |
| dossier ouvert | `applications` | 20 | 100 | mois civil UTC |
| lettre écrite | `applications` | 30 | 150 | mois civil UTC |
| paragraphe réécrit | `applications` | 100 | 500 | mois civil UTC |
| message du chat | `offers` | 40 | 150 | 24 h glissantes |

- **Les plafonds sont du domaine** de chaque module (`cv/domain/quotas.py`,
  `applications/domain/quotas.py`, `offers/domain/dialogue.py`), jamais de la
  configuration (AGENTS.md § 6.3). Chaque module garde sa règle ; aucun
  n'importe l'autre (règle 4).
- **Seule une action rendue compte.** Le quota se vérifie *avant* le modèle
  (l'action de trop ne coûte rien) et se note *après* son succès (un refus ou
  une panne ne consomment rien). Rouvrir un dossier existant et relancer une
  adaptation échouée ne comptent pas. Un import se compte dès que le modèle
  a lu, qu'il en sorte un CV ou non : la lecture est payée.
- **Deux registres dédiés** (`cv_ai_usages`, `applications_ai_usages`, une
  ligne par action, colonne `kind`) et non les tables métier : une photo en
  attente s'efface dès qu'une autre est retenue, un dossier se supprime —
  ni l'un ni l'autre ne doit rendre ce qu'il a coûté. Le chat compte ses
  propres messages (`offers_messages`), qui ne s'effacent qu'avec le compte.
- **Le mois civil UTC** (remise à zéro le 1er à minuit UTC, 1 h ou 2 h à
  Paris), plus lisible qu'un mois glissant : « les prochains le 1er
  décembre ». Le chat garde sa fenêtre glissante de 24 heures, qui existait.
- **L'abonnement se lit chez `payments`** (`GetBillingOverview.ad_free`, grâce
  comprise) par un port `SubscriptionReader` dans chacun des trois modules,
  branché par `bootstrap/wiring.py` (`PaymentsSubscriptionReader`). Par défaut,
  personne n'est abonné : un oubli de branchement coûte à l'abonné, jamais à
  Karria. Le chat ne lit l'abonnement qu'au plafond du gratuit.
- **Le refus** : `429 rate_limited`, `details.reason` =
  `portrait_quota_exceeded`, `import_quota_exceeded`, `dossier_quota_exceeded`,
  `letter_quota_exceeded`, `paragraph_quota_exceeded` (avec `limit`,
  `subscriber`, `resets_at`, `retry_after`) ou `assistant_rate_limited` (avec
  `limit`, `subscriber`). Le message propose « Karria sans pub » au gratuit et
  parle d'usage raisonnable à l'abonné.
- **La lecture** : `GET /cv/me/photo/quota` (`subscriber`, `limit`, `used`,
  `remaining`, `resets_at`), sans accord d'IA requis — l'app affiche « 2
  portraits restants » au gratuit, rien à l'abonné.
- **Le frein de rythme des dossiers** (30 appels en 10 minutes, ADR-0033) reste :
  il arrête un script dans l'instant, le quota borne le mois.
- **À la suppression du compte**, les registres partent avec le reste
  (ADR-0015). Supprimer son compte et en recréer un remet les compteurs à
  zéro : plus coûteux pour la personne que pour nous.
- **Concurrence** : deux demandes simultanées à la dernière unité du mois
  passent toutes les deux. Une de trop, plutôt qu'un verrou.

Valeurs choisies sans mesure d'usage (deux personnes actives en production le
2026-11-16) : à revoir avec les premiers chiffres. Proposition initiale
écartée : « 10 paragraphes par dossier » — remplacée par un plafond mensuel,
plus simple à dire et à afficher.

**Pas de quota** pour la dictée, l'interprétation d'un chapitre et
l'explication ATS : chacune coûte moins de 0,2 c€ et dépend d'un geste déjà
borné. Les envois par e-mail ont le leur (5 par jour, ADR-0031).

## Conséquences

- Migration 23 (deux tables neuves, non destructive), à appliquer **avant** le
  code : sans elles, toute action soumise à quota répond 500.
- L'app lit `GET /cv/me/photo/quota`, traduit les nouvelles raisons, et
  l'offre « sans pub » dit « sans pub, sans limite* — *usage raisonnable ».
- Le chat passe de 120 à 40 messages par jour pour les personnes non abonnées.
