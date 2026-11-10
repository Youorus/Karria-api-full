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

### Les quotas anonymes du fil et de la galerie sont en mémoire, par processus

Même limite que le limiteur de connexion (ci-dessus) : chaque worker Gunicorn
compte seul, et un déploiement remet les compteurs à zéro. Le jour où
l'aspiration du fil se mesure, l'implémentation à écrire est un compteur Redis
derrière `SlidingWindowLimiter` — les routes ne changent pas (ADR-0025).

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

### L'adresse de contact des e-mails n'est pas confirmée (le logo l'est depuis le 2026-11-05)

Le logo des e-mails est désormais servi par notre stockage public
(`karria-production-assets/brand/…`, ADR-0032, vérifié en production).
`contact@karria.fr` (`shared/mail/theme.py`) n'est qu'un repli : l'adresse
citée est `MAIL__FROM_ADDRESS`, et personne n'a confirmé qu'une boîte la relève.

**Ce que ça coûte :** un pied qui promet une boîte que personne ne relève.
**Déclencheur :** avant le premier envoi réel (`scripts/test_smtp.py`).

### Le quota de rendus de l'atelier et le cache des URL de photos sont en mémoire

`documents` compte les rendus de l'atelier (90/min/compte, ADR-0029) et garde
les URL signées des photos stables 45 min **par processus** (Gunicorn en a
quatre) : le plafond réel est multiplié par le nombre de workers, et une
photo peut être retéléchargée jusqu'à quatre fois par la WebView.

**Ce que ça coûte :** rien de visible en un exemplaire ; un peu de bande
passante mobile.
**Déclencheur :** la réplication de l'API, ou une plainte sur la consommation
de données. Un compteur et un cache partagés (Redis) derrière les mêmes
fonctions.

### Les pictogrammes de contact ne suivent pas la couleur choisie (ADR-0034)

Les icônes à gauche des lignes de contact sont rastérisées depuis le PDF du
modèle (`icons[*].file`, PNG à 600 dpi) : `recolor_spec` ne les voit pas, elles
gardent la couleur d'origine quand la personne change la couleur principale.
Faire : teinter le PNG au rendu (remplacer les pixels de la couleur d'origine,
alpha conservé) dans `html_renderer`/`docx_renderer` quand `primary_color` est
posée, ou retenir les tracés vectoriels à l'analyse.

### `pages` du rendu de l'atelier est une estimation

Le nombre de pages rendu par `POST /documents/{id}/render` vient d'une mesure
sans navigateur ; la vérité est dans `window.karriaFit` une fois les polices
chargées dans la WebView. Un CV à la limite peut annoncer 1 et en faire 2.

**Ce que ça coûte :** une seconde de décalage dans l'indicateur de pages de
l'app, qui lit ensuite `window.karriaFit`.
**Déclencheur :** si l'app ne peut pas lire la WebView (plateforme sans
`injectedJavaScript`) ; passer alors par Gotenberg au repos, plus lent.

---

## Module `cv`

### L'édition directe est servie par une seule route (fait le 2026-11-04)

`PUT /cv/me/chapters/{chapter}` remplace une section d'un bloc ; les routes
V1 (`PUT /cv/update`, `PUT /cv/image`, `/cv/experiences/{id}`…) ne sont
**pas** reprises nom pour nom : l'application v2 n'en a qu'une à appeler.
Ce qui reste : un `CvUpdated` sur l'identité ou le contact (aucun module ne
consomme `CvFinalized` aujourd'hui, l'événement n'est publié qu'à la
finalisation et à l'import) ; l'e-mail du compte comme repli quand l'import
ne lit aucun contact (cela demande un port vers `identity`).

### L'import ne diffuse pas sa progression

L'extraction est **un** appel au modèle (10 à 30 s) ; l'application affiche
une attente honnête puis révèle ce qui a **vraiment** été lu (`kept`,
`discarded`, `missing`). Diffuser des lignes pendant l'appel demanderait un
flux (SSE) et le décodage de JSON partiel côté modèle ; la V1 le faisait en
SSE. **Déclencheur :** des imports jugés trop longs à l'écran.

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

## Modèles de CV et exports (ADR-0020)

### Le module est construit ; ce qui reste à mesurer ou à décider

**Fait (2026-10-24) :** module `documents`, Gotenberg 8.38.1 en production, polices
gérées (bibliothèque libre gardée, substitution mesurée et déclarée), DOCX avec
ornements et polices incorporées (Meridian 0,88, Minimalist 0,84), poste visé
toujours sous le nom, score ATS lu par pdfminer. Voir ADR-0020 § « Mise en œuvre ».

### Les puces du DOCX sont des glyphes, pas des formes

Les puces du DOCX sont de vraies listes Word (« • » de la police du texte, au corps du
texte) : la personne ajoute une mission d'un retour à la ligne. Le glyphe est plus petit
que la pastille dessinée du modèle (0,5 mm contre 1,06 mm sur Minimalist), et la mesure
« décor » du DOCX ne compte pas les glyphes (Meridian 0,45, Minimalist 0,32). **Ce qu'il
faudrait :** une puce image (`w:lvlPicBulletId`) ou une police de puces incorporée.
**Déclencheur :** la revue Fable, ou un retour de Marc sur l'aspect du DOCX.

### Ingestion v2 (ADR-0037) : ce que le DOCX ne rend pas encore

Le DOCX empile la colonne latérale d'une frise (entreprise, dates) **au-dessus** du contenu de
l'élément, dans la marge de gauche, au lieu de la poser à côté ; et il pose les filets de bande
ou de flux à leur hauteur mesurée (Word n'étire pas une forme avec un tableau). **Ce qu'il
faudrait :** un tableau imbriqué par élément de frise (deux cellules), et une forme ancrée à la
cellule avec sa hauteur relue après mise en page. **Déclencheur :** un retour de Marc sur le
DOCX d'un modèle à frise.

### Ingestion v2 : une famille du même nom, mais une autre fonte

Canva sert « League Spartan » dans sa fonte de 2014 (The League of Moveable Type, graisse unique),
18 à 20 % plus large que la League Spartan de Google (2021, redessinée). La résolution
« même famille, bibliothèque libre » la prend, et l'écart de chasse part en interlettrage
(0,6 à 1,3 mm par lettre sur cv9) : les mots restent à leur place, les titres se lisent espacés
et dépassent 0,1 em (variante « optimisée ATS » proposée). **Ce qu'il faudrait :** quand la
chasse de la famille homonyme s'écarte de plus de 10 % de la police incorporée sur toutes ses
lignes, la tenir pour une autre fonte et chercher la remplaçante par métriques parmi toutes les
familles (ou ajouter l'ancienne League Spartan, OFL, au catalogue). **Déclencheur :** un
second modèle dans ce cas, ou un retour de Marc sur cv9.

### Ingestion v2 : les barres de niveau (langues) sont du décor

Des barres de niveau (une piste claire, un remplissage) sont ancrées à la rubrique à leur
place mesurée : deux langues dans l'exemple, deux barres. Depuis le 2026-11-10 elles sont
**propres à l'exemple** (`sample_only`) : une personne n'en a aucune, plutôt que des barres
posées sur ses mots. **Ce qu'il faudrait :** un rôle de « jauge » par élément, rempli d'après
le niveau du CV. **Déclencheur :** un retour de Marc.

### Ingestion v2 : une fonte homonyme aux métriques verticales différentes (cv3)

Canva incorpore « BebasNeueBold » (Dharma Type, boîte de ligne 9,5 mm en 27 pt) ; la
bibliothèque sert la Bebas Neue de Google (la même graisse renommée « Regular », boîte 11,4 mm).
Même famille, même chasse, mais les glyphes se posent 1 à 2 mm plus bas dans leur boîte : le
surligneur mesuré sous un titre se retrouve dessus. Même famille de problème que League
Spartan ci-dessus, par la hauteur cette fois. **Ce qu'il faudrait :** comparer aussi les
métriques verticales (`hhea`/`OS/2`) de la famille homonyme à celles de la police incorporée,
et corriger la position du texte de l'écart d'ascendante. **Déclencheur :** cv3 passe sinon
(0,87 : Biro Script Plus et TT Commons Pro sont par ailleurs substituées).

### Ingestion v2 : un tracé derrière un texte ne se rastérise pas

Un tracé complexe (≥ 8 mm, non rond) se rend en image rastérisée depuis la source, sauf s'il
passe sous un texte (le texte y serait deux fois). Un surligneur sous un titre (cv3) reste
donc une forme : rectangle arrondi à la boîte peinte, sans la texture du trait. **Ce qu'il
faudrait :** rastériser la page sans le texte (pdfium ne l'offre pas) ou vectoriser le tracé en
SVG. **Déclencheur :** un modèle où la forme compte vraiment (une vague de couleur derrière un
titre).

### Ingestion v2 : les rubriques libres d'un vrai CV

Une rubrique libre d'un modèle (« Réalisations clés », « Certifications ») est gardée avec son
titre et rendue dans l'exemple ; le CV v2 n'a pas de rubrique libre, donc elle n'a rien à
afficher pour une personne. **Ce qu'il faudrait :** une liste de rubriques libres dans le CV
(titre + entrées), que `domain/view.py` apparierait par titre. **Déclencheur :** la décision de
Marc d'ouvrir le CV à des rubriques libres.

### Marketing reste en revue

Ingestion v2 (2026-11-10) : 0,915, mots à 2 mm 0,99, mais SSIM tolérant 0,857 sous le
plancher 0,88 (polices de bibliothèque, pas de substitution) : les filets gris clair pleine
largeur sous les titres et le bloc « références » sous un second titre « LANGUAGES » (défaut du
**modèle**). **Ce qu'il faudrait :** signaler les titres de rubrique en double à
l'ingestion, ou que Marc corrige le modèle Canva ; ou juger ce modèle sur son score global
(≥ 0,90) plutôt que sur la composante SSIM. **Déclencheur :** le lot
`~/Desktop/Modele de CV/v2/cv1…cv10`.

### Les URL signées visent l'adresse interne du stockage

Sans `STORAGE__PUBLIC_ENDPOINT_URL`, les URL présignées (aperçus, exports) sont
signées pour `karria-minio:9000`, injoignable depuis un téléphone. **Ce qu'il faudrait :**
DNS `storage.karria.fr` → serveur, domaine Dokploy (Traefik, Let's Encrypt) vers le
service MinIO port 9000, puis la variable dans l'API et le worker. **Déclencheur :**
immédiat (Marc).

### La seconde page n'est pas mesurée sur un vrai CV long

Au-delà de l'ajustement (espacements ×0,6, corps ×0,88), le flux déborde sur une seconde
page sans rompre proprement les colonnes. Les mots-clés manquants s'affichent sans
accents (forme repliée). **Déclencheur :** un CV réel qui dépasse une page ; la première
revue du score ATS par Marc.

## Entreprises et logos (ADR-0021)

### Les icônes SVG ne sont pas rastérisées

Un site qui ne déclare qu'une icône SVG (Lidl) retombe sur le service d'icônes de
Google, souvent trop petit (48–60 px, refusé). Rastériser demanderait `resvg`
ou `cairosvg` : à peser contre le gain mesuré (`scripts.companies coverage`).

### Un logo prêt n'est jamais rafraîchi

Une entreprise qui change de logo garde l'ancien. Une passe mensuelle sur les
logos de plus de 90 jours, quand la source donne une adresse nouvelle, suffirait.

### La résolution DNS n'est pas épinglée

`HttpLogoFetcher` vérifie que chaque hôte résout vers une adresse publique, puis
httpx résout de nouveau : un DNS malveillant qui change entre les deux passerait.
Épingler l'adresse (transport httpx sur IP + en-tête `Host`, SNI) le fermerait.

### LinkedIn ne voit que les cibles les plus prioritaires

Son budget (40 recherches/h) est consommé par les premières cibles de chaque
passage ; les cibles moins prioritaires ne passent jamais par LinkedIn. Un
tourniquet par site dans le planificateur le corrigerait si la couverture
LinkedIn de la longue traîne s'avère utile.

### Les types `cv_ready`, `cv_export`, `template(s)`, `assistant`, `account` n'ont pas d'émetteur

Le contrat push v1 (ADR-0022) les définit, l'app les route, mais **aucun
module ne les envoie** au 2026-10-25 : l'export d'un CV est synchrone
(201 en 1,5 à 3,9 s), la finalisation du CV aussi. **Déclencheur :** le
premier travail long (export en file, `ats_explain` long, adaptation à une
offre) — il appelle un port « prévenir » branché sur `devices`, avec
`PushMessage(kind=PushKind.CV_EXPORT, target_id=export_id, …)`.

## Relecture du 2026-10-29 (Fable) — ce qui reste après les correctifs

### Le DOCX est pénalisé pour ses propres tableaux de mise en page

`docx_renderer.py` pose chaque bande dans un tableau sans bordure, même à une
colonne ; `ats_reader.read_docx` compte les `<w:tbl>` et `_layout` retire un
point à chaque DOCX. **Ce qu'il faudrait :** rendre une bande à une colonne sans
tableau, ou ne pas compter ceux qu'on produit. **Déclencheur :** la revue du
score ATS par Marc.

### La galerie des modèles lit une version par modèle

`GET /templates/` appelle `get_version` par modèle (plus trois signatures d'URL) :
un N+1 sans conséquence à trois modèles, visible à trente. **Ce qu'il
faudrait :** `versions_by_ids`, ou les clés d'aperçu sur `TemplateModel`.

### Le plancher de 250 mots du contrôle « contenu » pénalise les CV juniors

Un CV d'une page en français en a souvent 150 à 250. **Ce qu'il faudrait :**
150, ou un barème selon l'expérience. **Déclencheur :** le premier retour d'une
personne sur son score.

### LinkedIn ne voit que les cibles prioritaires (inchangé)

Voir « Entreprises et logos » ci-dessus ; 3 001 offres LinkedIn actives au
29/10, aucun blocage.

### Le comptage exact du fil « toutes les offres »

`page_active` compte les offres actives à chaque page (80 ms sur 243 000 lignes,
balayage parallèle) ; il croît avec le corpus. **Ce qu'il faudrait :** une
estimation (`reltuples`) ou un compte mis en cache une minute.
**Déclencheur :** 500 000 offres, ou un p95 du fil au-dessus de 300 ms.

### L'aiguillage du bus pour les paiements

`HandleWebhook` journalise et ne livre rien (ADR-0023). **Déclencheur :** la
décision de Marc sur l'achat intégré ; alors un événement `PaymentCompleted`
sur le bus, consommé par `documents`.

---

## Encaissements (ADR-0024)

### Aucune réconciliation planifiée avec Stripe

La projection des abonnements n'est mise à jour que par les webhooks. Un
webhook perdu (point de terminaison mal configuré, panne longue de l'API au
moment de l'envoi, Stripe réessaie trois jours) laisserait `payments_accounts`
diverger de Stripe jusqu'à la notification suivante — un abonné résilié verrait
encore « sans pub », ou l'inverse.

**Ce que ça coûte :** rare, borné à un cycle de facturation.
**Déclencheur :** le premier écart constaté, ou le premier millier d'abonnés.
Un travail `payments.reconcile` nocturne dans le worker (`subscriptions.list`
par client, `BillingAccount.synced`) ; le worker recevrait alors les variables
`PAYMENTS__*`.

### Aucun courriel de Karria autour d'un paiement — sauf le merci

Les reçus sont ceux de Stripe (à activer dans le tableau de bord). Depuis le
2026-11-05, chaque livraison vaut un courriel et une notification de merci
(ADR-0024 § 2026-11-05), envoyés hors transaction et jamais rejoués par
Stripe : un échec se lit dans le journal (`payments.thanks_*_failed`) et se
rattrape à la main (`scripts/payments_thanks.py`). Si ces échecs se
multiplient, passer le merci par la file durable. Karria ne dit toujours rien
par e-mail à un prélèvement en échec : la personne le découvre dans l'app
(« paiement en attente ») et par le reçu d'échec de Stripe.

**Déclencheur :** une plainte, ou l'ouverture du compte de messagerie
transactionnel de Karria pour d'autres usages.
