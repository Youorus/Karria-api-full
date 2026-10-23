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

---

## Moteur d'offres (ADR-0016)

### Aucune purge des offres inactives

Une offre expirée quitte l'index mais reste en base (on la relit si elle
est sauvegardée).

**Ce que ça coûte :** ~5 Ko par offre ; à 30 000 nouvelles par jour,
~4,5 Go par mois.
**Déclencheur :** 2 millions de lignes. Un travail nocturne qui supprime les
offres inactives depuis 90 jours que personne n'a sauvegardées ni
candidatées (et leurs entrées de fil).

### On ne sait pas si une annonce est encore en ligne

JobSpy ne rend que les annonces des 72 dernières heures : une offre pourvue
reste visible jusqu'à 30 jours après sa dernière parution. Pour France
Travail, `GET /offres/{id}` rend 204 quand l'annonce est retirée (vérifié),
et le contrat de remise sait déjà le dire (`expired: true`, désactivation
et retrait de l'index côté API, testés) — mais la **boucle de vérification**
du collecteur n'est pas écrite : il lui faudrait une route interne qui
rende les identifiants à vérifier (les plus anciens d'abord) et une colonne
`liveness_checked_at`.

**Ce que ça coûte :** des offres mortes dans le fil, surtout en fin de vie ;
pour France Travail, 10 appels/s permettraient de vérifier 30 000 annonces
actives en moins d'une heure par jour.
**Déclencheur :** des retours « offre introuvable ». Écrire la route et la
boucle ; pour JobSpy, vérifier l'URL à l'ouverture du détail (HEAD) et
désactiver sur 404/410.

### La part nouvelle d'une journée courante n'est pas encore mesurée

Mesuré : 79 % au premier remplissage, 57 % sur le passage du lendemain
(même matinée). Le régime de croisière se lit dans `offers_ingestion_runs`
(`inserted / fetched`).

**Ce que ça coûte :** le modèle de coût de l'ADR-0016 suppose 40 %.
**Déclencheur :** une semaine de collecte réelle ; relire la table et
corriger l'ADR par une nouvelle.

### L'enrichissement du résidu n'est pas écrit

L'ADR-0018 § 6 décide un enrichissement *gratuit d'abord* (champs
structurés, règles, classifieurs sur plongements, modèle pour le résidu).
Les mesures sont faites (`docs/evals/classifiers_e5.md`) ; rien n'est
branché : les annonces JobSpy sans contrat, temps ou expérience restent
`null`, et la ligne des traits dans `embedding_text()` fait que tout
classifieur sur le vecteur indexé triche.

**Ce que ça coûte :** les filtres contrat/temps ignorent une part des
annonces JobSpy (passage de l'inconnu selon `strict_schedule`).
**Déclencheur :** trois décisions de Marc — `scikit-learn` dans l'image,
retirer les traits du texte plongé (ré-indexage ~50 min) ou second
plongement, et quels champs valent un appel au modèle (le contrat oui,
l'expérience probablement non). Ensuite : un travail de file
`offers.enrich` par lots, en cache, hors ingestion.

### Le journal d'apprentissage n'a ni purge ni oubli, et rien ne s'entraîne encore

L'ADR-0019 consigne chaque geste (`offers_interaction_events`). Rien ne le
lit encore : les modèles attendent l'accord de Marc sur `scikit-learn`
(et `lightgbm`). Le journal n'est pas effacé à la suppression de compte ni
purgé avec le temps.

**Ce que ça coûte :** ~200 000 lignes par jour à 1 000 personnes actives ;
une donnée orpheline après suppression de compte.
**Déclencheur :** l'accord sur les dépendances → `scripts/learning_export.py`,
travail `offers.retrain` nocturne avec porte de promotion et dérive ; dans
tous les cas, étendre `ForgetUser` au journal et purger à 180 jours.

### Le ré-indexage `t3` n'a pas été re-jugé

`embedding_text` a perdu la ligne des traits (ADR-0019 § 3). La précision@10
jugée (91,4 % en `t2`) n'a pas été remesurée après le ré-indexage, par
sobriété. **Déclencheur :** avant de promouvoir un classement appris, rejouer
`scripts/offers_eval.py --judge` une fois.

### L'analyse de chaque offre par Karria n'existe pas

L'app appelle `GET/POST /job-matches/{id}/summary(-status)` (V1) ; le
contrat de recherche ne les sert pas, `job_offer.summary` vaut `null`.

**Ce que ça coûte :** l'écran de détail affiche « Analyse non disponible ».
**Déclencheur :** la décision de Marc sur ce module (coût Gemini par offre
ouverte, mise en cache par offre et par personne).

### La porte d'intitulé perd les titres inattendus

« Barber » n'entre pas pour « coiffeuse », ni « Chef de projet » nu pour
« chef de projet digital ». C'est du rappel perdu, jamais une erreur
montrée.

**Ce que ça coûte :** 5 demandes sur 35 sans résultat le 2026-10-22, dont
une par synonyme manquant (les quatre autres : le corpus n'avait rien).
**Déclencheur :** des fils vides alors que le corpus a l'offre. Pistes :
demander au modèle des intitulés plus variés ; ou un reclasseur juste
(`bge-reranker-v2-m3`) sur les seuls candidats refusés, hors requête.

### MinIO n'est pas épinglé

`karria-minio` tourne sur `quay.io/minio/minio:latest` ; l'image locale n'a
pas d'empreinte de registre et le dernier tag n'est plus publié sur quay.

**Ce que ça coûte :** un redéploiement de la composition peut changer de
version de MinIO sans qu'on l'ait décidé.
**Déclencheur :** le prochain changement de la composition `karria-donnees`.
Choisir une image maintenue et l'épingler par empreinte.

### La porte d'intitulé accepte un titre de niveau supérieur

« Chef cuisinier / Cheffe cuisinière » passe pour « cuisinier », « Cuisinier
chef de partie » aussi : la porte exige que les mots demandés soient dans le
titre, elle ne refuse rien de ce qui s'y ajoute. Mesuré le 2026-10-22 : 3 à
4 des 10 premières de « cuisinier en temps plein dans toute la France ».

**Ce que ça coûte :** des offres d'un niveau au-dessus dans le fil, que le
juge (et la personne) tiennent pour hors sujet.
**Déclencheur :** la validation de l'ADR-0017. Piste : une liste de mots de
**niveau** (« chef », « responsable », « directeur », « aide », « assistant »,
« adjoint », « stagiaire ») qui éliminent quand ils ne sont pas dans la
demande ; à mesurer avec `scripts/offers_eval.py --judge`.

### La réécriture d'une cible rétrogradée et la boucle des synonymes

Une cible à l'essai qui ne rapporte rien, ou dont les titres ne passent pas
la porte, est rétrogradée et journalisée (`offers.target_demoted`,
`offers.target_offtarget`) ; les rejets de la porte se comptent
(`gate_rejects`). Personne ne lit encore ces signaux : ni réécriture du
terme par le modèle, ni enrichissement des synonymes depuis les ouvertures,
sauvegardes et rejets.

**Ce que ça coûte :** une cible mal nommée reste mal nommée ; un métier que
les annonces titrent autrement (« hôte de caisse » pour « caissière ») n'est
appris que si le modèle le sait déjà.
**Déclencheur :** une semaine de production ; lire les cibles rétrogradées
et les `gate_rejects` les plus hauts, puis proposer au modèle une réécriture
à partir des titres réels rapportés.

### Le dédoublonnement par URL de partenaire n'est pas fait

France Travail republie des offres de partenaires avec un lien vers eux :
517 annonces sur 30 244 (1,7 %) pointent Indeed. Le condensat et le SimHash
n'en rapprochent que 41 (texte reformaté).

**Ce que ça coûte :** jusqu'à 1,7 % du flux France Travail en double avec
Indeed dans un fil.
**Déclencheur :** une part au-delà de 5 %, ou des retours de doublons.
Normaliser les URL de candidature (sans paramètres de suivi), les indexer,
et les ajouter au niveau 1 du dédoublonnement.

### Le modèle d'échelle du planificateur n'est pas vérifié

L'ADR-0017 projette le nombre de cibles et de requêtes à 1 000, 10 000 et
100 000 personnes sur un modèle (1,35 terme par personne, concentration de
la demande, 4 passages par jour par cible active).

**Ce que ça coûte :** une estimation, pas une mesure.
**Déclencheur :** une semaine de production ; relire `offers_scrape_targets`
(`scrapes`, `yield_ema`) et `offers_target_demands`.

### Le modèle de plongement n'a pas de remplaçant mesuré

e5-small int8 irait trois fois plus vite (44,8 annonces/s) ; sa qualité
sur nos demandes n'a pas été mesurée.

**Ce que ça coûte :** rien tant que le volume tient (20,7 offres/s de bout
en bout, ×4 la cible).
**Déclencheur :** un volume ×3, ou un serveur plus petit.

---

## Migration depuis la V1

### Les jetons push V1 sont des jetons FCM bruts, inutilisables par Expo

La V1 enregistrait un jeton Firebase par compte (`users.fcm_token`,
`push_provider = fcm`). Dans la sauvegarde du 2026-09-12, **aucun** des
19 jetons n'a la forme `ExponentPushToken[…]` : ce sont des jetons
d'enregistrement FCM (format `id:APA91b…`, 142 caractères), 13 sous iOS et
6 sous Android. L'expéditeur V2 (`devices`, ADR-0012) parle à Expo, qui ne
connaît que ses propres jetons : `scripts/migrate_v1.py import` les compte
(`push_unusable:fcm`) et n'en déclare aucun appareil.

**Ce que ça coûte :** la campagne « Karria est de retour » ne peut joindre
que les personnes qui auront réinstallé l'app et accordé la permission —
l'app V2 déclare alors son appareil (`PUT /devices/{installation_id}`) et
`scripts/push_campaign.py`, relancé, les trouve. Personne n'est prévenu
« à froid ».
**Ce qu'il faudrait pour les joindre à froid :** un envoi FCM direct
(HTTP v1) avec le compte de service du projet Firebase V1, derrière une
seconde implémentation du port `PushGateway` — et l'app V1 installée côté
personne, puisque le jeton lui appartient. Téléverser les identifiants FCM
dans Expo ne suffit pas : Expo n'accepte pas un jeton FCM brut en entrée.
**Déclencheur :** décider que la relance « à froid » vaut le compte de
service Firebase et une passerelle de plus. Sinon, laisser mourir avec
l'app V1.
