# Module `devices` — appareils et notifications push

Le contexte **« par où joindre cette personne »**. Il sert le contrat
`docs/contrat-api-appareils.md` de `karria_app` : `PUT /devices/{id}`,
`DELETE /devices/{id}`, et une route d'essai hors production.

## Ce qu'il possède

- **Les installations** (`devices_installations`) : une fiche par couple
  `(compte, installation_id)` — plateforme, jeton Expo, permission, OS,
  modèle, version de l'app, langue, fuseau, région, première et dernière
  déclaration, pays d'après `CF-IPCountry`. **Jamais une adresse IP.**
- **Les tickets Expo** (`devices_push_tickets`) en attente de reçu.
- **La règle `push_enabled`** : jeton présent **et** permission accordée.
- **Le service d'envoi** `SendPushToUser` : à tous les appareils
  joignables d'un compte, par lots de cent, tickets mémorisés, jetons morts
  détachés (ADR-0012). Il ne prend qu'un `PushMessage` du contrat
  `shared/push` (ADR-0022) et le traduit en message Expo : `data` du
  contrat, `channelId` et `categoryId` (`offres` / `documents` / `compte`),
  `badge` pour les offres. La route d'essai envoie un `offers_feed`
  marqué `campaign: "test"`.
- **La lecture des reçus** `CheckPushReceipts`, appelée par
  `scripts/push_receipts.py`.
- **Des lectures de support** sur le dépôt : `main_device_of(user_id)`,
  `platform_stats()`. Pas de route : personne n'en a besoin à l'écran.
- **L'appareil principal d'un compte** (`GetMainDevice` : plateforme et
  version de l'app, rien de plus) : ce que `bootstrap/wiring.py` sert au
  module des comptes pour la trace de départ (ADR-0015).
- **L'oubli d'un compte supprimé** (`ForgetUserDevices`) : toutes les
  installations du compte partent, jetons compris. Abonné à
  `AccountDeleted` par `bootstrap/wiring.py` ; idempotent.

## Ce qu'il ne possède pas, et où ça vit

- **Le compte** : `identity`. `user_id` est un UUID sans clé étrangère ;
  `devices` ne lit pas la table d'un autre module.
- **Quoi dire, et quand** : le module qui a quelque chose à annoncer. Il
  déclare un port « prévenir cette personne » que `bootstrap/wiring.py`
  branche sur `devices.get_send_push_to_user`. Aucun gabarit, aucun seuil
  ici.
- **Le transport** : l'API push d'Expo, derrière `PushGateway`, via le
  client HTTP du socle. Pas de Firebase.
- **La déconnexion** : `identity` ferme la session, l'app appelle
  `DELETE /devices/{id}` elle-même, avant. La **suppression du compte**,
  elle, n'attend rien de l'app : l'événement suffit.

## Les choix qu'on ne devine pas en lisant le code

- **`PUT` remplace la fiche entière.** Le serveur ne fusionne rien : ce
  que l'app n'envoie pas devient nul.
- **Un jeton mort est détaché de tous les appareils qui le portent**, quel
  que soit le compte : un jeton suit l'installation, qui a pu changer de
  compte sur un téléphone prêté.
- **Un lot n'est envoyé qu'une fois** (`attempts=1`) : retenter un lot
  accepté enverrait deux fois chaque notification. Une panne Expo lève
  `DependencyUnavailableError` ; l'appelant décide.
- **Les reçus se lisent un quart d'heure après l'envoi**, par la commande
  planifiée, jamais au démarrage. Sans elle, les désinstallations tardives
  ne seraient jamais vues.
- **La route d'essai répond 404 en recette et en production.** Ni 403, ni
  indice : elle n'existe pas là-bas.
- **La validation du jeton est plus stricte que le simulateur de l'app**
  (forme `ExponentPushToken[…]` exigée) : une erreur se voit à
  l'enregistrement, pas au premier envoi.
- **Les tickets Expo ne sont pas touchés à la suppression d'un compte** :
  ils ne portent pas de `user_id`, et se purgent avec la commande planifiée.
