# ADR-0026 — « Parler à l'équipe » : un fil par compte, un flux WebSocket réveillé par `LISTEN`/`NOTIFY`

**Statut :** Proposée — 2026-10-31 (écrite par Opus, à relire par Fable, à valider par Marc)
**Complète :** ADR-0012 (appareils et push), ADR-0016 (file de travaux dans PostgreSQL), ADR-0022 (contrat des notifications push)
**Référence :** V1 `karria_api/app/support/` (fil par utilisateur, lu/non-lu, routes admin, push à la réponse, courriel admin) ; l'app V1 relisait le fil **toutes les 60 s**.

## Le problème

Une personne doit pouvoir écrire aux **humains** de l'équipe Karria — texte,
message vocal, capture d'écran — et voir la réponse arriver **en direct**
quand l'écran est ouvert, être prévenue par une notification sinon. L'équipe
doit être prévenue de chaque nouveau message, lire et répondre sans outil
tiers.

La V1 faisait du *polling* toutes les 60 s : une minute de latence à l'écran,
une requête par minute et par écran ouvert pour rien, et la notification
partait même quand la personne lisait déjà.

## Décision

### 1. Un module `support`, sans IA

Un fil par compte (`support_conversations`), des messages (`support_messages` :
auteur `user`/`team`, sorte `text`/`voice`/`image`, texte **ou clé** de
fichier dans le bucket privé `support`, durée d'un vocal). Aucun modèle ne lit
ni n'écrit ici : la porte d'IA (ADR-0025) ne joue pas. Le vocal est **gardé
tel quel** (la V1 le transcrivait) : plus fidèle, et rien ne part chez un
fournisseur. La sorte d'un fichier vient de ses octets pour une image, du type
déclaré pour un son (`prepare_upload`), jamais d'un champ `kind` du client.

### 2. Le direct : un WebSocket qui **relit la base** quand on le réveille

`GET /api/v1/support/me/stream` (WebSocket, `Authorization: Bearer`, `?since=`
ISO 8601). Le serveur envoie `{"type":"message","message":…}` pour chaque
message postérieur au curseur et `{"type":"ping"}` toutes les 20 s ; il lit
`{"type":"read"}` pour poser la lecture. Jeton refusé : fermeture `4401`.

Le **réveil** passe par `shared/realtime` : un bus de réveils, pas une file de
messages. L'écriture d'un message publie le sujet `support:<conversation>` ;
chaque flux abonné à ce sujet relit `list_after(curseur)`. Le réveil ne porte
aucune donnée : il n'y a ni ordre à garantir ni perte à rattraper, et chaque
flux relit aussi au bout de 20 s sans réveil. **Un réveil perdu retarde, il ne
perd rien.**

En production, le bus est **PostgreSQL `LISTEN`/`NOTIFY`** sur un canal unique
(`karria_wakeups`, le sujet dans la charge) :

- `NOTIFY` part par la connexion Tortoise de la requête : dans une transaction,
  il ne part qu'à la validation, donc l'abonné réveillé trouve la ligne ;
- `LISTEN` tient **une** connexion asyncpg dédiée par processus, ouverte à la
  première souscription (pas au démarrage), rouverte à la souscription
  suivante si elle tombe ;
- il traverse les processus Gunicorn et les services Dokploy (une réponse
  écrite par la console, servie par un autre worker, réveille le flux).

### 3. Notifier sans doublon

- **Push à la personne** (type `support` du contrat v1, canal `compte`,
  `karria://equipe`) quand l'équipe répond, **sauf** si elle a lu il y a moins
  de 45 s (l'app pose la lecture dès qu'un message lui arrive en direct : écran
  ouvert = pas de push), et **sauf** si une réponse non lue l'attend déjà (une
  rafale de réponses = une notification). Le corps ne cite pas la réponse
  (écran verrouillé).
- **Courriel à l'équipe** (`SUPPORT__TEAM_EMAIL`, `mtakoumba@gmail.com` par
  défaut) quand la personne écrit, sauf si un courriel est parti il y a moins
  de 10 min et que l'équipe n'a pas rouvert le fil depuis.
- Côté app, la bannière interne d'une notification `support` n'est pas montrée
  quand l'écran de l'équipe est déjà ouvert.

### 4. L'équipe : un jeton, des routes, une console

`/internal/support/conversations` (liste, `awaiting_only`), `/{id}` (l'ouvrir
pose la lecture de l'équipe), `/{id}/messages` et `/{id}/messages/media`
(répondre), protégés par `SUPPORT__ADMIN_TOKEN` comparé en temps constant ;
vide → 401 pour tous. La console `GET /internal/support/console` est une page
HTML sans dépendance qui demande le jeton et le garde en `sessionStorage`
(jamais dans l'URL) ; elle relit la liste et le fil ouvert périodiquement.
Aucun rôle sur le compte (ADR-0007) : l'équipe n'est pas un compte Karria.

### 5. Ce que devient un compte supprimé

`identity.AccountDeleted` → `support.ForgetUserSupport` : fichiers du bucket,
puis messages et fil.

## Alternatives écartées

- **Polling (V1)** : latence d'une minute, trafic à vide, push en double.
- **SSE** : suffisait pour descendre, mais il fallait un second canal pour
  « j'ai lu », et l'`EventSource` de React Native n'est pas natif (dépendance
  de plus). Le `WebSocket` est natif dans React Native et accepte l'en-tête
  `Authorization`.
- **Redis Pub/Sub** : pas de Redis dans la pile de production (ADR-0016).
- **Un canal `LISTEN` par conversation** : `LISTEN`/`UNLISTEN` à chaque
  ouverture d'écran ; un canal unique filtré en mémoire est plus simple.
- **Transporter le message dans le `NOTIFY`** : limite de 8 000 octets, URL
  signées qui expirent, et un réveil perdu deviendrait un message perdu.

## Conséquences

- Une connexion PostgreSQL de plus par processus API qui a ouvert un flux.
- Un flux ouvert = une tâche asyncio et une relecture toutes les 20 s : à
  surveiller au-delà de quelques centaines d'écrans ouverts simultanément.
- Le direct ne marche que si le proxy laisse passer les WebSockets (Traefik
  de Dokploy : oui, sans réglage). S'il tombe, l'app se replie sur la relecture
  au retour au premier plan et la notification push : dégradé, pas cassé.
- Migration 14 (`parler_a_l_equipe`) et bucket `support` **avant** le code :
  l'API vérifie chaque bucket du registre au démarrage.
