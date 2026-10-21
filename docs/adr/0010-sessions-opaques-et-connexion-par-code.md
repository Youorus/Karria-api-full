# ADR-0010 — Sessions opaques à rotation, connexion par code e-mail, compte né de la preuve

**Statut :** Acceptée — 2026-10-21
**Complète :** ADR-0007

## Contexte

L'ADR-0007 a posé la couture d'autorisation — `Principal`,
`resolve_principal` — en reportant explicitement le mécanisme de session.
Le contrat de l'app (`auth.api.ts`, `serveur-simule.ts`) fixe la surface :
un code à six chiffres par e-mail, Apple, Google ; `access_token` +
`refresh_token` rendus dans le corps ; un refresh qui **tourne** et dont la
réutilisation coupe tout (`lib/http.ts` sérialise ses rafraîchissements
pour cette raison précise).

Il fallait décider **comment une session naît, survit, se prouve et
meurt**, et ce qu'est un compte. Trois contraintes :

1. l'app est **mobile** : pas de cookie, pas de BFF — les jetons vont dans
   le trousseau du système ;
2. l'inscription ne collecte que l'adresse (contrat) ; il n'y a **aucun
   mot de passe** ;
3. un utilisateur mobile saisonnier ne doit jamais avoir à se reconnecter.

`zoumani_api` (ADR-0010, 0017, 0034 de ce cadre) a tranché ces questions
et fait tourner la mécanique un an en production, avec un cookie et un BFF
qui ne s'appliquent pas ici.

## Décision

**1. Un jeton de session est opaque, aléatoire et stocké haché.**
`secrets.token_urlsafe(32)` produit le jeton ; la base ne conserve que son
SHA-256 poivré (`SECURITY__TOKEN_PEPPER`). Le jeton ne porte aucune donnée.

**2. Deux jetons, deux durées, une rotation.**

| Jeton | Durée | Rôle |
|---|---|---|
| Accès | 15 minutes | Prouve l'identité sur chaque appel (`Authorization: Bearer`) |
| Rafraîchissement | 365 jours **glissants** | Rend un nouveau couple |

Le rafraîchissement tourne à chaque usage. Un jeton déjà consommé qui
ressert **plus d'une minute** après la rotation révèle un vol : toutes les
sessions de la personne tombent (`token_reuse_detected`). Dans la minute,
le rejeu est refusé mais ne ferme rien — c'est un doublon réseau, pas un
voleur (ADR-0034 de Zoumani : treize fausses alertes en six semaines).

La fenêtre glissante d'un an fait que la session ne se perd **que** par
déconnexion, révocation ou une absence continue d'un an. Dix sessions
simultanées au plus ; au-delà, la plus ancienne cède la place.

**3. Un compte est une adresse e-mail prouvée, et rien d'autre.** Ni nom,
ni téléphone, ni date de naissance. Un seul type d'utilisateur, aucune
permission d'administration : un compte connecté a accès à tout.

**4. La validation du code crée le compte.** `/auth/email/start` répond
identiquement pour une adresse connue ou non — un code part dans les deux
cas — et `/auth/email/verify` retrouve ou crée le compte, horodate le
consentement envoyé « en continuant », ouvre la session, rend
`created_account`. Même chemin pour Apple et Google : identité prouvée →
compte → session.

**5. Le code vit dans le parcours.** Dix minutes de validité, cinq essais,
renvoi après trente secondes et quatre envois au plus — les valeurs du
simulateur de l'app. Un seul objet (`LoginChallenge`) porte l'adresse, le
condensat du code, les essais et les envois ; Karria n'a qu'un usage de
code, là où Zoumani en avait quatre.

**6. Le refus ne renseigne pas.** Parcours inconnu et parcours périmé
rendent la même erreur (`auth_login_challenge_expired`, 410). Jeton social
forgé et jeton expiré aussi (`auth_social_token_invalid`, 401).

**7. Deux codes d'erreur s'ajoutent au socle** : `gone` (410) et
`rate_limited` (429, toujours avec `details.retry_after`), pour dire au
client « recommence » et « attends », qu'aucun code existant ne disait.

## Justification

- **Opaque plutôt que JWT** : la révocation immédiate — déconnexion,
  vol — vaut mieux que l'absence d'état. Le prix est une lecture indexée
  par requête, connu et accepté.
- **Pas de mot de passe** : rien à deviner, rien à fuir. La preuve est la
  possession de la boîte mail ; Apple et Google en attestent une.
- **Un an glissant** : la règle attendue d'une app mobile n'est pas
  « rester connecté longtemps », c'est « ne jamais être déconnecté ». Ce
  qui borne un appareil volé n'a jamais été la durée mais la révocation.
- **Le compte naît à la preuve** : entre un code validé et la fin d'un
  formulaire, il n'y aurait ni compte ni session ; fermer l'app là
  obligerait à redemander un code (ADR-0017 de Zoumani). Il n'y a d'ailleurs
  plus de formulaire.
- **Aucune dépendance** pour les sessions : `secrets`, `hashlib`, `hmac`.

## Conséquences

- `identity` possède comptes, rattachements Apple/Google, sessions et
  parcours ; quatre tables, une migration.
- `bootstrap/wiring.py` branche `ResolveSession` sur `resolve_principal` :
  les routes de tous les modules sont authentifiées sans changer.
- Le limiteur de tentatives est **en mémoire**, par processus : correct en
  un exemplaire, à remplacer par un compteur Redis le jour de la
  réplication (`PLUS-TARD.md`).
- Les sessions closes et les parcours périmés s'accumulent jusqu'à une
  commande de purge, à écrire (`PLUS-TARD.md`).
- `SECURITY__TOKEN_PEPPER` est obligatoire en environnement déployé ; le
  changer déconnecte tout le monde.

## Alternatives écartées

- **JWT signé sans état** — impossible à révoquer avant expiration.
- **Code séparé du parcours (`OneTimeCode`)**, comme Zoumani — une table
  de plus pour un seul usage. À réintroduire si un second usage apparaît
  (changement d'adresse, suppression de compte).
- **Durée courte de rafraîchissement (30 jours)** — déconnecterait
  l'utilisateur saisonnier, exactement celui que Karria veut revoir.
- **Mot de passe en option** — un second moyen d'entrer, une surface de
  fuite, et rien dans le contrat ne le demande.
