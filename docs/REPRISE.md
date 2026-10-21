# Reprise — état de `karria_api_v2`

> Document de passation. À lire en premier par toute personne ou IA qui
> reprend le travail, avec `AGENTS.md`. Mis à jour à chaque fin de session.

**Dernière mise à jour : 2026-10-21**

## Où on en est

Nouvelle API de Karria, « très similaire à la V1, écrite parfaitement ».
Socle repris de `zoumani_api` (monolithe modulaire FastAPI, Tortoise +
aerich, uv, mypy strict, import-linter). La V1
(`~/PycharmProjects/karria_api`) sert de référence fonctionnelle, jamais
de code à copier.

L'app cliente est `~/WebstormProjects/karria_app`. Ses contrats en prose
font foi : `karria_app/docs/contrat-api-*.md`.

| Module     | État | Routes (préfixe `/api/v1`) |
| ---------- | ---- | -------------------------- |
| `health`   | fait | santé |
| `identity` | fait | `POST /auth/email/start\|resend\|verify`, `POST /auth/social/google\|apple`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`, `DELETE /auth/me` |
| `devices`  | fait | `PUT/DELETE /devices/{installation_id}`, `POST /devices/me/test-notification` (local/test) ; service interne `SendPushToUser` (Expo) |
| `cv`       | fait | `GET /cv/me`, `GET/POST /cv/draft`, `POST /cv/draft/interpret`, `PUT /cv/draft/chapters/{c}`, `POST /cv/draft/finalize`, `POST /cv/photo`, `POST /cv/import`, `POST /voice/transcribe` |

Qualité au 2026-10-21 : `make check` est vert. 794 tests passent et 4
sont ignorés (les tests Gemini en direct). La couverture est de 97 %, et
de 100 % sur les modules métier.

**Rien n'est commité depuis le socle** (`5447036`). Les modules
`identity`, `devices` et `cv` ne sont pas suivis par git. Marc commite
lui-même, sur sa demande explicite.

## Décisions clés (détail dans `docs/adr/`)

- **Sessions (ADR 0010)** : modèle Zoumani.
  - Jeton d'accès de 15 minutes.
  - Jeton de rafraîchissement opaque, haché, renouvelé à chaque usage, glissant sur 365 jours.
  - La réutilisation d'un jeton déjà renouvelé révoque toute la session (60 s de grâce).
  - La session n'est perdue qu'à la déconnexion.
- **Google et Apple (ADR 0011)** :
  - vérification RS256 contre les clés publiques du fournisseur (JWKS) ;
  - contrôle de `aud`, `iss`, `exp` et du nonce (sha256 pour Apple) ;
  - le compte est rattaché par `sub` ;
  - un compte = un e-mail prouvé, sans nom ni prénom.
- **Appareils (ADR 0012)** :
  - un appareil par (utilisateur, installation) ;
  - aucune IP stockée, le pays vient de l'en-tête `CF-IPCountry`.
- **Gemini (ADR 0013)** :
  - accès par un port et l'API REST, sans SDK ;
  - réponses à schéma imposé, revalidées par Pydantic ;
  - le texte de l'utilisateur est traité comme une donnée, jamais comme une consigne.
  - Modèles : `gemini-3.8-flash` (texte, audio, extraction) et `gemini-3.1-flash-image` (photo).
- **CV (ADR 0014)** : sections en JSONB ; la photo est référencée par `image_id`, jamais par une URL signée.
- **Suppression de compte (ADR 0015)** :
  - suppression physique ;
  - l'événement `AccountDeleted` est consommé par `devices` et `cv` via des ponts dans `bootstrap/wiring.py` ;
  - la raison du départ est gardée de façon anonyme (`identity_departure_feedback`).

## Lancer en local

```bash
cd ~/PycharmProjects/karria_api_v2
unset VIRTUAL_ENV            # un venv d'un autre projet perturbe uv
make up                      # Postgres + MinIO + migrations + buckets
uv run uvicorn karria.main:app --reload --host 0.0.0.0 --port 8001
```

- Port **8001** : le 8000 est pris par l'API V1 locale.
- `.env` est hors git et déjà rempli : Gemini (`GEMINI__API_KEY`) et les
  identifiants OAuth Google (`SOCIAL__*`, repris de la V1, bundle
  `com.karria.app`).
- Ne jamais afficher ces valeurs.
- Mail en mode `console` : le code de connexion s'affiche dans le journal du serveur.

## Ce qui reste (par priorité)

1. **Tester sur le simulateur** :
   - connexion Google et Apple réelle (Marc doit la faire, une IA ne peut pas taper) ;
   - parcours CV complet ;
   - photo améliorée ;
   - import.
2. **Modules suivants**, à décider par Marc un par un : offres/matching
   (`job-matches`), modèles de CV, assistant, génération de CV (PDF),
   abonnement Stripe.
3. **Points listés dans `docs/PLUS-TARD.md`** :
   - purge planifiée ;
   - limiteur de débit et cache JWKS multi-réplicas (Redis) ;
   - livraison durable des événements ;
   - durée de conservation des raisons de départ ;
   - routes d'édition directe du CV ;
   - chapitre `projects`.
4. **Hébergement et déploiement** : `docs/DEPLOIEMENT.md` n'est qu'un squelette.

## Règles de travail (Marc)

- Ne jamais commiter ni pousser sans demande explicite.
- Économiser les tokens : agir avec précision et lancer des agents
  seulement pour de vrais chantiers. À la limite de session, s'arrêter
  proprement et reprendre à la session suivante.
- E-mail administrateur : `mtakoumba@gmail.com`.
- Ne jamais publier ni afficher de secret (`~/karria-archive` en contient en clair).
- Mettre ce fichier à jour à chaque fin de session.
