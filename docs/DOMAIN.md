# Découpage en contextes métier

> **Statut : non écrit.** Ce document ne propose **aucun** découpage en
> modules et aucun ordre de construction : Marc décide module par module
> (AGENTS.md §9). Il recense seulement les **surfaces visibles du contrat**
> de l'application, pour qu'on sache de quoi on parle quand un module est
> nommé. Le jour où un découpage est tranché, il s'écrit ici, avec l'ADR
> qui le valide.

## Le métier en trois phrases

Karria est une plateforme d'emploi assistée par IA. Une personne dit ce
qu'elle cherche — en conversation, comme à un ami — et reçoit chaque jour
un fil d'offres sélectionnées pour elle, chacune analysée. Elle construit
son CV en conversation avec Gemini, l'adapte à chaque offre, suit ses
dossiers de candidature.

## Les deux références

- **La V1 `karria_api`** (`~/PycharmProjects/karria_api`) : ce que l'API
  fait, ses routes, ses données, ses noms de champs. Référence
  fonctionnelle, contre-exemple technique.
- **L'app `karria_app`** (`~/WebstormProjects/karria_app`) : le contrat,
  en exécutable (`src/lib/dev/serveur-simule.ts`) et en prose
  (`docs/contrat-api-*.md`, `docs/reference-v1.md`).

## Les surfaces du contrat, telles que l'app les attend

Relevé du 2026-10-20. Ce ne sont **pas** des modules : une surface peut
tenir dans un module, ou se partager entre deux.

| Surface | Routes vues dans le contrat | Source |
|---|---|---|
| Compte et session | `POST /auth/email/start`, `/auth/email/resend`, `/auth/email/verify`, `/auth/social/google`, `/auth/social/apple`, `/auth/refresh`, `/auth/logout`, `GET /auth/me`, `DELETE /auth/me` | `serveur-simule.ts`, `contrat-api-compte.md` |
| Fil d'offres matchées | `GET /job-matches/me` (filtres, pagination, `has_next`), `/job-matches/me/filter-options`, `GET /job-matches/{id}`, `…/summary-status`, `POST …/summary`, `PATCH …/interaction`, SSE `/job-matches/me/stream`, `PATCH /job-matches/bulk-feed-seen`, `POST …/dismiss`, `GET /job-matches/feedback-tags` | `serveur-simule.ts`, `reference-v1.md` §7 |
| Agent conversationnel | `POST /assistant/messages` (contrat app) ; V1 : `POST /karria/chat/converse`, `GET /karria/chat/search/{id}/status\|matches` | `serveur-simule.ts`, `reference-v1.md` §6 |
| CV | `GET /cv/me`, `PUT /cv/update`, `PUT /cv/image`, sous-ressources `/cv/experiences`…, `POST /cv/ai/…`, brouillon en conversation `GET/POST /cv/draft`, `POST /cv/draft/interpret`, `PUT /cv/draft/chapters/{chapter}`, `POST /cv/draft/finalize` | `contrat-api-cv.md` |
| Voix et photo | `POST /voice/transcribe`, `POST /generated-images/upload`, `POST /generated-images/generate` | `contrat-api-cv.md` |
| Dossiers de candidature | `GET /job-applications`, `GET /cv/me/generated-cvs` | `reference-v1.md` §6 |
| Modèles de CV | `GET /templates/?active_only=true`, `/user-templates/me` | `reference-v1.md` §6 |
| Profil, quotas, abonnement | `/users/me`, consommation et quotas, abonnement | `reference-v1.md` §6 |
| Offres publiques (invité) | `/public/job-offers` | `reference-v1.md` §6 |

## Les contextes construits

Nommés par Marc, module par module. Le reste du découpage n'est pas écrit.

| Module | Surface du contrat servie | ADR |
|---|---|---|
| `identity` | Compte et session : `/auth/*`, dont la suppression `DELETE /auth/me` | 0010, 0011, 0015 |
| `devices` | Appareils et notifications push : `/devices/*` | 0012 |
| `cv` | Le CV en conversation : `/cv/*`, `/voice/transcribe` | 0013, 0014 |
| `support` | « Parler à l'équipe » : `/support/me*` (dont le WebSocket `/support/me/stream`), `/internal/support/*` et la console | 0026 |

## Ce qui est déjà fixé, quelle que soit la découpe

- **Un seul type d'utilisateur**, identifié par son adresse e-mail. Aucun
  `role`, aucun `is_admin` sur le compte (ADR-0007).
- **Les erreurs** suivent `{ error: { code, message, details: { reason, field } } }`
  (`shared/errors/`), `details.reason` portant la raison fine que l'app
  branche.
- **Les fichiers** vont dans un bucket, la base ne garde que la clé, l'API
  rend une URL signée sous le nom de champ que la V1 exposait (`image_url`).
- **Un appel à un modèle** vit derrière un port, en `infrastructure/`, et
  sa sortie est validée contre un schéma (AGENTS.md §6.14).
