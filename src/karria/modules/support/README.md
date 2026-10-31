# Module `support` — « Parler à l'équipe »

Le contexte **« écrire à des humains »**. Une personne laisse un mot à
l'équipe Karria — un texte, un vocal, une capture d'écran — et l'équipe lui
répond dans le même fil, qu'elle voit arriver **en direct** si l'écran est
ouvert, et dont elle est prévenue sinon. Il sert
`docs/contrat-api-equipe.md` de `karria_app`.

Ce n'est **pas** Karria l'IA : aucun modèle ne lit ni n'écrit ici, et la
porte d'IA (ADR-0025) ne joue pas.

## Ce qu'il possède

- **Les conversations** (`support_conversations`) : une par compte, ouverte
  à la première visite, jamais close. Elle retient les derniers instants qui
  décident de tout : dernier mot de la personne, dernière réponse de
  l'équipe, dernière lecture de chaque côté, dernier courriel envoyé.
- **Les messages** (`support_messages`) : auteur (`user` / `team`), sorte
  (`text` / `voice` / `image`), le texte ou la **clé** du fichier dans le
  bucket `support` (jamais une URL, AGENTS §6.12), la durée d'un vocal.
- **Les règles de notification** (`domain/conversations.py`) :
  - *push à la personne* quand l'équipe répond, **sauf** si elle a l'écran
    ouvert (lecture il y a moins de 45 s — l'app marque lu dès qu'un message
    lui arrive en direct) ou si une réponse non lue l'attend déjà (une
    rafale = une notification) ;
  - *courriel à l'équipe* quand la personne écrit, **sauf** si un courriel
    est parti depuis moins de 10 min et que l'équipe n'a pas rouvert le fil
    (une rafale = un courriel).
- **Le flux en direct** `GET /support/me/stream` (WebSocket) : relit la base
  à chaque réveil du bus `shared/realtime` et toutes les vingt secondes
  (ADR-0026). Le client y pose sa lecture (`{"type": "read"}`).
- **Les routes de l'équipe** `/internal/support/*` (jeton
  `SUPPORT__ADMIN_TOKEN`, comparé en temps constant) et **la console**
  `/internal/support/console`, une page HTML sans dépendance.
- **L'oubli d'un compte supprimé** (`ForgetUserSupport`) : fichiers puis fil.

## Ce qu'il ne possède pas, et où ça vit

- **Le compte et son adresse** : `identity`. Le module ne connaît qu'un
  `user_id` ; l'adresse montrée à l'équipe vient du port `AccountDirectory`,
  branché dans `bootstrap/wiring.py`.
- **L'envoi des notifications** : `devices`, par le port `UserNotifier`.
  Le type `support` du contrat push (`shared/push`) ouvre
  `karria://equipe`.
- **Le courriel** : `shared/mail`, message `SUPPORT_NEW_MESSAGE`, adressé à
  `SUPPORT__TEAM_EMAIL`.
- **Le réveil entre processus** : `shared/realtime` (`LISTEN`/`NOTIFY`).

## Les choix qu'on ne devine pas en lisant le code

- **Le vocal est un fichier, pas une transcription.** La V1 transcrivait la
  voix en texte ; on l'écoute désormais telle quelle — c'est plus fidèle, et
  cela n'envoie rien à un fournisseur d'IA.
- **La sorte d'un fichier vient de ses octets** (`prepare_upload`), pas du
  champ `kind` : l'app n'en envoie pas.
- **Le curseur du flux est l'instant de création** (microseconde). Deux
  messages d'un même fil à la même microseconde est un cas qu'on accepte de
  ne pas distinguer.
- **Le flux ne renvoie pas l'historique** : l'app le charge par `GET
  /support/me`, puis ouvre le flux avec `since=` l'instant du dernier message
  reçu. Un message arrivé entre les deux est rattrapé.
- **La console garde le jeton dans `sessionStorage`**, jamais dans l'URL :
  un lien de courriel n'emporte que l'identifiant du fil (`#id`).
- **Le corps du push ne cite pas la réponse** : l'écran verrouillé d'un
  téléphone prêté n'est pas à nous.
