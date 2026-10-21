# Ce qui est repoussé, et pourquoi

Ce fichier recense **tout ce qui a été identifié puis remis à plus tard**,
avec sa raison. Il n'existe pas pour se donner bonne conscience : une
chose qu'on décide de ne pas faire doit être écrite, sans quoi elle sera
redécouverte dans six mois, sous forme de panne.

Chaque entrée dit ce qui manque, **ce que ça coûte de ne pas le faire**,
et ce qui déclencherait de s'en occuper. Une entrée sans conséquence
identifiée n'a rien à faire ici : elle se supprime.

Tenu à jour à mesure — toute décision de reporter s'y consigne.

---

## Socle

### Le limiteur de tentatives et le cache JWKS sont en mémoire

`identity` borne les tentatives de connexion par une fenêtre glissante en
mémoire (`InMemoryLoginThrottle`) et garde les clés publiques Apple/Google
dans un cache par processus (`HttpJwksSource`).

**Ce que ça coûte :** rien en un exemplaire. Avec N répliques derrière un
répartiteur, le plafond de tentatives est multiplié par N.
**Déclencheur :** la première réplication de l'API. Écrire alors un
compteur Redis derrière le port `LoginThrottle` ; le use case ne change pas.

### Aucune purge des sessions closes, des parcours et des tickets

Les dépôts offrent `purge_ended_before`, `purge_before` et
`purge_checked_before` ; aucune commande ne les appelle.

**Ce que ça coûte :** des tables qui grossissent — une ligne par parcours
de connexion, une par session close, une par ticket Expo vérifié.
**Déclencheur :** le premier déploiement avec de vrais utilisateurs. Un
`scripts/purge.py` planifié, à côté de `scripts/push_receipts.py`.

### L'oubli après suppression de compte n'est pas durable

`AccountDeleted` traverse le bus in-process (ADR-0015). Si le processus
tombe entre la transaction d'`identity` et la publication, ou si le
stockage refuse une suppression d'objet, `devices` ou `cv` gardent des
lignes orphelines — `user_id` sans compte — journalisées
(`cv.forget_incomplete`, `event_bus.handler_failed`). Le compte lui-même
est bien parti.

**Ce que ça coûte :** une donnée personnelle qui reste en base ou dans le
bucket jusqu'à intervention, alors que la personne a demandé l'effacement.
**Déclencheur :** le premier déploiement avec de vrais utilisateurs. Écrire
`scripts/purge.py` (ou une tâche planifiée) qui liste les `user_id` de
`cv_*` et `devices_*` absents d'`identity_users` et rejoue `ForgetUser` /
`ForgetUserDevices` — tous deux déjà idempotents. Le motif *outbox*
viendra avec le deuxième événement qui doit absolument aboutir.

### La rétention de la trace de départ n'est pas décidée

`identity_departure_feedback` est anonyme, mais son mot libre contient ce
que la personne a voulu écrire. Rien ne borne sa conservation.

**Ce que ça coûte :** rien tant que la table reste petite et interne.
**Déclencheur :** la première analyse produit qui s'en sert. Décider alors
d'une durée (douze mois ?) et d'une purge du mot libre, la raison et l'âge
pouvant rester indéfiniment.

### Aucune reprise durable des envois push

Un lot refusé par une panne Expo lève `DependencyUnavailableError` ;
l'appelant décide. La V1 avait une file de reprise persistante.

**Ce que ça coûte :** une notification perdue pendant une panne Expo.
**Déclencheur :** le premier module qui envoie des notifications dont la
perte compte (une offre fraîche se rattrape au prochain fil ; un message
de l'équipe, moins). Relève du motif *outbox* (voir `shared/events`).

### Les identifiants Google ne sont pas posés

Le `.env` de `karria_app` porte `EXPO_PUBLIC_GOOGLE_*_CLIENT_ID` **vides**
(constaté le 2026-10-21). L'API n'a donc rien à recopier :
`SOCIAL__GOOGLE_*_CLIENT_ID` sont vides, et `/auth/social/google` répond
`503 auth_social_provider_unavailable`.

**Ce que ça coûte :** pas de connexion Google tant que les clients OAuth
ne sont pas créés dans la console Google et recopiés des deux côtés.
**Déclencheur :** la création des clients OAuth (iOS + web) du projet.

### `docs/DEPLOIEMENT.md` est un squelette

La V1 tournait sur Scaleway et Dokploy, puis a été éteinte (septembre
2026). Le cadre `zoumani_api` déployait par `compose.prod.yaml`. Rien de
cela n'est repris : la cible d'hébergement de la refonte n'est pas décidée.

**Ce que ça coûte :** rien avant le premier déploiement.
**Déclencheur :** la décision d'hébergement. Écrire alors la séquence, et
un `compose.prod.yaml` si c'est le choix.

### Le logo d'e-mail et l'adresse de contact ne sont pas confirmés

`shared/mail/theme.py` pointe `https://karria.fr/icon-512.png` et
`contact@karria.fr`. Ni l'un ni l'autre n'a été vérifié.

**Ce que ça coûte :** un en-tête d'e-mail avec un carré vide, et un pied
qui promet une boîte que personne ne relève.
**Déclencheur :** avant le premier envoi réel (`scripts/test_smtp.py`).

---

## Module `cv`

### Les routes d'édition directe de la V1 ne sont pas servies

`PUT /cv/update`, `PUT /cv/image` et les sous-ressources (`/cv/experiences`,
`/cv/skills`…) existent dans la V1 et sont citées par le contrat comme
« restant ». Le module ne sert aujourd'hui que la création en conversation,
l'import et la lecture : la seule façon de modifier un CV est de refaire un
brouillon.

**Ce que ça coûte :** une faute de frappe dans un CV finalisé oblige à
rouvrir le parcours. **Déclencheur :** l'écran d'édition du CV dans
l'application. Les schémas et le dépôt sont prêts à les recevoir.

### Les projets ne sont jamais alimentés

La section `projects` de la V1 est conservée dans le CV (schéma, colonne,
réponse) mais aucun chapitre de la conversation ni l'import ne la remplit :
le contrat n'a pas de chapitre « projets ».

**Ce que ça coûte :** rien pour le parcours actuel. **Déclencheur :** un
chapitre `projects` dans le contrat, ou son extraction à l'import.

### La photo d'un CV importé n'est pas récupérée

L'import lit le texte et les sections ; il ne découpe pas la photo du
document pour la proposer au chapitre `photo`.

**Ce que ça coûte :** la personne reprend sa photo depuis sa galerie.
**Déclencheur :** une demande explicite ; cela demanderait un rendu du PDF
en image et une détection de visage.

### Les PDF partent en texte, pas en image

Constaté le 2026-10-21 : Gemini lit un PDF joint comme une image et en
perd la moitié ; la couche texte, elle, est extraite intégralement. Les PDF
avec couche texte partent donc en texte (mise en page perdue), les scans
partent tels quels. Un rendu page par page en image donnerait le meilleur
des deux, au prix d'une dépendance de rendu (`pypdfium2`).

**Ce que ça coûte :** un CV en colonnes peut voir son ordre de lecture
mélangé dans la couche texte. **Déclencheur :** des imports visiblement
mal découpés dans les journaux (`cv.imported` avec peu de chapitres).

### Aucun quota ni coût suivi sur les appels Gemini

Chaque interprétation coûte un appel ; rien ne borne le nombre d'appels
par personne et par jour, et l'usage (`usageMetadata`) n'est que
journalisé.

**Ce que ça coûte :** un client qui boucle peut faire une facture.
**Déclencheur :** le module des quotas (« le même compte porte les
quotas »), qui portera la règle ; ce module exposera son usage par
événement ou par port.
