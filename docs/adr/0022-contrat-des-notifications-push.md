# ADR-0022 — Un contrat typé et versionné pour toute notification push

**Statut :** Proposée — 2026-10-25 (demande de Marc : « un deep linking digne d'une grande entreprise »)
**Complète :** ADR-0012 (appareils et notifications par Expo)

## Le problème

Chaque émetteur composait son `data` à la main : les alertes d'offres
`{"type": "NEW_OFFERS", "offer_id", "count"}`, la campagne de retour
`{"screen": "feed", "campaign"}`, l'essai `{"type": "TEST"}`. L'app, elle,
n'ouvrait que `data.url` — qu'aucun n'envoyait. **Une alerte touchée
n'ouvrait rien.** Rien n'empêchait un quatrième format au prochain émetteur.

## Décision

1. **Un seul constructeur, `shared/push`** (`PushKind`, `PushMessage`). Les
   émetteurs sont dans plusieurs modules (`offers`, `devices`, scripts) que la
   règle 4 empêche de s'importer ; le contrat est un format d'échange avec
   l'app, sans règle métier — comme les gabarits de `shared/mail`. Pur stdlib :
   le domaine d'`offers` l'importe sans enfreindre la règle 2 bis.
2. **Le format v1** (miroir de `karria_app/docs/contrat-notifications.md`) :
   `data` en chaînes — `v` (`"1"`), `type`, `id` (obligatoire pour `offer` et
   `template`, permis pour `cv_export`, refusé ailleurs), `count` (offres),
   `campaign` (mesure), `url` (lien équivalent pour les versions de l'app qui
   ne lisent que lui). Hors `data` : `channelId` = `categoryId` déduits du
   type (`offres`, `documents`, `compte`), `badge` 1 pour les offres.
3. **La validation est à la construction** : un `offer` sans identifiant lève
   chez l'émetteur, pas dans `devices`. Les bornes du titre et du corps vivent
   dans le contrat ; `devices.PushNotification` les revérifie (défense).
4. **Ports** : `offers.UserNotifier.notify(user_id, message)` et
   `devices.SendPushToUser.execute(user_id=…, message=…)` prennent un
   `PushMessage` — plus aucun `title/body/data` libre.

## Conséquences

- Un nouveau type = une valeur de `PushKind`, son canal et son chemin dans
  les deux tables du contrat, **et** la même ligne côté app. Le test
  `tests/unit/shared/test_push.py` les énumère tous.
- Changer la forme (`v` = 2) se fait additivement : l'app v1 lit encore `url`.
- Les canaux Android doivent exister sur l'appareil : l'app crée `offres`,
  `documents`, `compte` (un `channelId` inconnu ferait taire la notification).
