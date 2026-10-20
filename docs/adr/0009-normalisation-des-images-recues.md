# ADR-0009 — Toute image reçue est normalisée avant d'être écrite

**Statut :** Acceptée — 2026-10-20, reprise du cadre `zoumani_api`
**Contexte :** `shared/images`, `shared/storage`, toutes les routes qui reçoivent un fichier

## Le problème

Plusieurs routes reçoivent des images — photo de profil, photo à
améliorer pour le CV, et demain tout document photographié. Dans la V1,
chacune vérifiait le type **annoncé** contre une liste et un plafond de taille,
puis écrivait les octets tels quels. Conséquences constatées :

- un HEIC d'iPhone est refusé, ou pire, accepté sous une étiquette
  `image/jpeg` que l'application pose par défaut, et rangé illisible ;
- une photo de 6 Mo est refusée au lieu d'être réduite, sur un réseau
  où l'envoi a déjà coûté ;
- l'orientation EXIF n'est pas appliquée : une photo prise en portrait
  s'affiche couchée selon le client ;
- les métadonnées restent, coordonnées GPS comprises, sur des photos
  de profil prises au téléphone. C'est un problème de confidentialité avant
  d'être un problème de poids.

## Ce qu'on décide

### 1. Un socle image, sans métier

`shared/images` reconnaît une image à ses **octets**, jamais au type
annoncé ; refuse ce qui n'en est pas une, et ce qui ferait exploser la
mémoire une fois décodé (bombe de décompression, plafond de 50
mégapixels) ; redresse selon l'EXIF ; retire toutes les métadonnées ;
réduit à une plus grande dimension ; recompresse par paliers — en
qualité d'abord, en dimensions ensuite — jusqu'à tenir sous le plafond
d'octets ; convertit vers JPEG, sauf transparence à conserver (PNG).

Une image déjà conforme — JPEG sans EXIF, dans les dimensions, sous le
plafond — ressort **intacte**. Chaque réencodage dégrade, et les photos
de l'application arrivent déjà compressées : les réencoder serait payer
en qualité une garantie déjà acquise.

### 2. La politique appartient au bucket

Jusqu'où réduire, à quelle qualité, avec quel plancher : c'est une
propriété de **l'usage**, et l'usage est le bucket. `BucketPolicy.image`
porte une `ImagePolicy` à côté des types acceptés et du plafond :

| Bucket | Plus grand côté | Qualité | Plancher | Raison |
|---|---|---|---|---|
| `documents` | 3 000 px | 92 | 80 | un CV photographié se **lit** ; une ligne floue ne s'extrait pas |
| `media` | 2 048 px | 85 | 60 | ce qu'un écran de téléphone affiche |

Un bucket sans politique ne retouche rien.

### 3. Un point d'entrée, avant la clé

`shared.storage.prepare_upload(bucket, octets, type annoncé)` est ce que
chaque route appelle **avant** de construire la clé et d'écrire. C'est
ce qui fait que l'extension de la clé et le type enregistré chez le
fournisseur disent la vérité sur les octets. Ce qui n'est pas une image
— un PDF, une note vocale — passe intact avec son type annoncé, et la
politique du bucket le juge ensuite comme avant. Ce qui **prétend** être
une image sans en être une est refusé ici, avec une consigne.

Le chemin d'envoi direct de la photo de profil — l'API n'a jamais vu les
octets — est retraité à la **confirmation** : l'objet est relu,
normalisé, et réécrit sous la même clé s'il a changé.

### 4. Ce qu'on refuse, et ce qu'on laisse passer

Refusé : ce qui n'est pas une image lisible alors que c'en est une qui
est attendue, ce qui dépasse 50 mégapixels, ce qui ne tient pas sous le
plafond même réduit à 480 pixels de large. Chaque refus dit quoi faire.

Laissé passer : un fichier tronqué par une coupure réseau se décode
jusqu'où il va — une photo avec une bande grise vaut mieux qu'un envoi à
refaire sur un réseau qui a déjà coupé.

## Les dépendances

**Pillow** (`pillow`) : la bibliothèque d'images de référence en Python.
Ses roues embarquent libjpeg, libpng et libwebp — rien à installer dans
l'image Docker `python:3.13-slim`. **pillow-heif** : le HEIC des iPhone
récents, que Pillow ne lit pas seul, avec libheif embarquée de la même
façon.

Écarté : libvips (pyvips), plus rapide et plus sobre en mémoire, mais
qui exige une bibliothèque système et un second adaptateur ; pour
quelques dizaines d'images par jour, le gain ne vaut pas la dépendance.
Écarté aussi : ne rien décoder et déléguer la conversion à l'application
mobile — le serveur ne peut alors ni garantir l'absence de métadonnées,
ni protéger un client web ou un futur client qu'on ne contrôle pas.

Les deux importent `PIL` dans **un seul fichier**, `shared/images/pillow.py`,
derrière des fonctions qui ne rendent aucun type de la bibliothèque.

## Ce que cela coûte

- Une décodage par image reçue, quelques dizaines de millisecondes sur
  une photo de téléphone, dans le worker qui la reçoit. Acceptable au
  volume actuel ; le jour où il ne l'est plus, c'est une tâche différée.
- Une clé d'objet dont l'extension peut mentir sur le chemin d'envoi
  direct, quand le client annonçait un format et en a envoyé un autre :
  la clé est opaque et le type enregistré est le bon.
- Les tests qui envoyaient des octets qui *commencent* comme un JPEG
  envoient désormais de vraies images, fabriquées par `sample_image`.
