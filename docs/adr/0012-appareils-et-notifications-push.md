# ADR-0012 — Appareils déclarés par installation, notifications par Expo

**Statut :** Acceptée — 2026-10-21

## Contexte

La V1 (`app/push_notifications/`) rangeait **un** jeton FCM sur le compte,
envoyait par Firebase Admin avec un fichier de compte de service, mêlait
dans un même module la file de reprise, les gabarits « nouvelle offre »,
les seuils de pertinence et la désactivation des jetons morts. Un compte,
un jeton : deux téléphones s'évinçaient l'un l'autre.

L'app Expo obtient désormais un **jeton Expo** sans configuration Firebase,
et son contrat (`docs/contrat-api-appareils.md`,
`src/lib/dev/appareils-simule.ts`) fixe `PUT/DELETE /devices/{installation_id}`.

## Décision

**1. Un appareil est une installation, nommée par l'app.** Le couple
`(compte, installation_id)` est unique. L'app déclare sa fiche entière à
chaque démarrage (`PUT`, idempotent) ; le serveur la remplace, retient la
première et la dernière déclaration, et le pays si le mandataire le
transmet (`CF-IPCountry`). **Aucune adresse IP** n'est conservée.

**2. `push_enabled` est la décision du serveur** : jeton présent **et**
permission `granted`. L'app l'affiche, ne le recalcule pas.

**3. Le transport est le service push d'Expo**, derrière un port
(`PushGateway`), via le client HTTP du socle. Lots de cent au plus, une
seule tentative par lot (un envoi n'est pas idempotent). Les **tickets**
sont mémorisés ; les **reçus** sont lus un quart d'heure plus tard par une
commande planifiée (`scripts/push_receipts.py`), jamais au démarrage.

**4. Un jeton déclaré mort se détache tout seul** — `DeviceNotRegistered`
sur un ticket ou sur un reçu — de **tous** les appareils qui le portent,
quel que soit le compte : un jeton suit l'installation, qui a pu changer de
compte. L'appareil reste connu, sans jeton, jusqu'à sa prochaine
déclaration.

**5. Un service d'envoi, pas de politique.** `SendPushToUser.execute(user_id,
title, body, data)` achemine ce qu'on lui donne à tous les appareils
joignables du compte. Il ne décide ni du moment, ni de la pertinence, ni du
texte : cela appartient au module qui a quelque chose à dire, derrière un
port à lui que `bootstrap/wiring.py` branchera sur
`devices.get_send_push_to_user` (règle 4).

**6. La route d'essai `POST /devices/me/test-notification`** n'existe
qu'en local et en test (`404` ailleurs), exige la session et ne vise que
l'appelant.

## Justification

- **Plusieurs appareils par compte** : c'est la réalité d'un téléphone et
  d'une tablette, et la V1 les faisait s'évincer.
- **Expo plutôt que Firebase** : c'est le jeton que l'app produit ; un
  compte de service Firebase est un secret et une dépendance de plus pour
  un chemin que l'app n'emprunte plus.
- **Les reçus** : Expo accepte un message avant de savoir si le système le
  livrera ; un appareil désinstallé n'est signalé que là. La V1 les
  ignorait et ne détachait qu'à l'erreur immédiate.
- **Pas de gabarits ni de seuils ici** : les constantes de la V1
  (`NOTIFY_SCORE_FLOOR`, fenêtres anti-rafale) sont des décisions produit
  du fil d'offres, qui n'existe pas encore.

## Conséquences

- Deux tables : `devices_installations`, `devices_push_tickets`. Pas de clé
  étrangère vers `identity_users` : `devices` ne lit pas la table d'un
  autre module.
- `PUSH__EXPO_ACCESS_TOKEN` est facultatif (sécurité renforcée du projet
  Expo) ; `PUSH__EXPO_API_URL` permet de viser un faux serveur en recette.
- Aucune file de reprise : un lot refusé par une panne Expo lève
  `DependencyUnavailableError` et l'appelant décide. Une reprise durable
  viendra avec le besoin (`PLUS-TARD.md`).

## Alternatives écartées

- **Un jeton sur le compte** (V1) — un seul appareil joignable à la fois.
- **Firebase Admin** — secret de plus, dépendance lourde, et le jeton Expo
  ne lui parle pas directement.
- **Stocker l'adresse IP** pour géolocaliser — un historique de
  déplacements que personne n'a demandé ; le pays du mandataire suffit au
  support.
