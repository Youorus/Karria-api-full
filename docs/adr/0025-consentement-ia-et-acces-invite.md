# ADR-0025 — Consentement explicite à l'IA avant le premier usage, et lecture du fil sans compte

**Statut :** Proposée — 2026-10-30 (à valider par Marc)
**Complète :** ADR-0007 (le `Principal` et la couture `resolve_principal`), ADR-0013 et ADR-0018 (la couche LLM), ADR-0015 (suppression de compte)
**Contexte :** revue App Store — règles 5.1.2 (i) (données partagées avec des tiers) et 5.1.1 (v) (connexion exigée pour un contenu qui n'est pas lié au compte)

## Le problème

Deux exigences d'Apple, qui touchent toute l'API :

1. **5.1.2 (i).** Karria envoie des données de la personne — ses messages,
   sa voix, son CV, sa photo — à des fournisseurs de modèles tiers
   (`shared/llm` : Google Gemini aujourd'hui, Anthropic et OpenAI possibles).
   Avant la **première** utilisation d'une fonctionnalité qui le fait, la
   personne doit voir ce qui part, chez qui, pourquoi, et l'accepter
   explicitement. « En continuant, tu acceptes » ne suffit pas, et un
   consentement que l'app vérifierait seule ne vaut rien : un client peut
   l'oublier.
2. **5.1.1 (v).** Les offres d'emploi ne sont pas un contenu de compte :
   Apple a déjà refusé Karria pour avoir exigé la connexion au lancement.
   Le fil, le détail d'une offre et la galerie des modèles doivent se lire
   **sans compte**.

## Décision

### 1. Un consentement à l'IA, versionné par ce qu'on dit à la personne

Le module `identity` porte un **consentement à l'IA**, distinct des
conditions : une ligne par compte (`identity_ai_consents`) avec la version
acceptée, les fournisseurs et usages nommés, la date, et la date de retrait
s'il a eu lieu. Routes : `GET/PUT/DELETE /auth/me/ai-consent`, et le champ
`ai_consent` de `GET /auth/me`.

La **version se calcule** : `<révision>.<empreinte>` où la révision
(`AI_DISCLOSURE_REVISION`) change quand la formulation change, et
l'empreinte dérive des fournisseurs **réellement routés** pour les tâches
qui reçoivent des données de la personne (`LLM__TASKS__*`,
`LLM__FALLBACKS__*`, fournisseurs appelables seulement) et des usages
(`AiPurpose`). Un fournisseur ou un usage de plus change la version : le
consentement ne couvre plus l'information courante et l'app redemande. On
ne peut pas oublier de le faire.

Les usages, tels que la personne les reconnaît : parler à Karria (dialogue
et mémoire), dicter, créer le CV en conversation, importer un CV, améliorer
la photo, expliquer le score ATS d'un export. `enrich` et
`template_extract` n'y sont pas : ils traitent des offres et des modèles
déposés par l'équipe, jamais une donnée de la personne.

### 2. La porte est dans le socle, la politique dans `identity`, le branchement dans `bootstrap`

`shared/auth` offre `get_ai_consent_gate` et `AiConsentedPrincipalDep`,
**refusés par défaut** (403 `ai_consent_required`) — la même couture que
`resolve_principal` (ADR-0007). `bootstrap/wiring.py` y branche
`identity.EnsureAiConsent`. Les modules `cv`, `offers` et `documents`
dépendent de la porte, jamais d'`identity` (règle 4).

Routes sous la porte : `POST /assistant/messages`, `POST /cv/draft/interpret`,
`POST /cv/import`, `POST /voice/transcribe`, `POST /cv/photo` (seulement
avec `enhance=true` — sans amélioration, la photo ne quitte pas Karria),
`POST /cv/me/generated-cvs` (le score ATS est expliqué par un modèle).
La réponse porte `details.version` et `details.providers` : l'app sait quoi
montrer sans un second appel.

Le retrait ne supprime pas la ligne : il pose `revoked_at`. Les deux dates
font partie de la preuve ; la suppression du compte emporte tout (ADR-0015).

### 3. Le fil et la galerie se lisent sans compte, en lecture seule et sous quota

`GET /job-matches/me`, `/me/filter-options`, `/{offer_id}` et
`GET /templates/`, `/templates/{id}`, `/{id}/reviews` acceptent un appelant
sans session. Sans compte : `all_offers` seul, aucune personnalisation,
aucun état, aucun journal, et les filtres personnels (`saved_only`,
`unseen_only`, `unapplied_only`) répondent `401 account_required`. Tout ce
qui écrit — sauvegarder, écarter, « vu », compter une vue, posséder,
exporter, parler à Karria — exige toujours la session.

Les mêmes gestionnaires répondent sous `/offers/feed`,
`/offers/filter-options`, `/offers/{id}` : un nom qui ne dit plus « me ».
`/job-matches/me` reste servi tel quel pour l'app installée.

Un appelant anonyme est **compté par origine** (`shared/http/ratelimit.py`,
fenêtre glissante en mémoire) : 240 lectures par 10 minutes pour les offres,
120 pour les modèles — des quotas qui appartiennent au module, pas au socle.
Au-delà : `429 anonymous_rate_limited` avec `retry_after`. Une personne
connectée n'est pas comptée : son identité est déjà une limite. La même
réserve que le limiteur de connexion (ADR-0010) : par processus, donc par
réplique.

## Alternatives écartées

- **Vérifier le consentement dans l'app seulement.** Un client modifié, ou
  un oubli dans un nouvel écran, enverrait des données sans accord. Le
  serveur est la seule porte qu'on ne peut pas contourner.
- **Un booléen `ai_consent` sur le compte.** Sans version, on ne saurait
  plus qui a accepté quoi quand le fournisseur change ; sans fournisseurs
  enregistrés, l'information donnée ne serait pas prouvable.
- **Une version décrétée à la main.** Elle serait oubliée au premier
  changement de `LLM__TASKS__*` — précisément le cas qu'Apple vise.
- **Un module `consent` séparé.** Un module de plus pour une table : le
  consentement est une propriété du compte (règle 6.5).
- **Des routes publiques distinctes avec leur propre code.** Deux
  gestionnaires divergeraient ; un seul, avec `Principal | None`, garde le
  contrat identique connecté ou non.

## Conséquences

- Migration 13 (`identity_ai_consents`), **à appliquer avant** le code.
- Toute nouvelle route qui confie une donnée de la personne à un modèle
  **doit** dépendre d'`AiConsentedPrincipalDep` ; une nouvelle tâche LLM
  qui traite une donnée de la personne **doit** entrer dans
  `PURPOSE_TASKS` — sinon elle ne figure pas dans l'information donnée.
- Les contrats de l'app : `contrat-api-consentement-ia.md` (nouveau),
  `contrat-api-offres.md` et `contrat-api-modeles.md` (accès anonyme),
  `contrat-api-compte.md` (`/auth/me`).
- Un fil public attire l'aspiration : le quota par origine est la première
  réponse ; un compteur partagé (Redis) viendra avec la réplication
  (`PLUS-TARD.md`).
