# L'atelier CV — feuille de route

*Rédigée le 2026-11-04 (Fable), à valider par Marc avant de construire.*

## Ce qu'on veut

Une personne a rempli ses informations (import ou conversation). Elle doit pouvoir
**créer un CV** : choisir un modèle, le voir **en temps réel avec ses propres données**,
toucher n'importe quel élément du CV pour le corriger, laisser Karria proposer ou
faire elle-même, puis **finaliser** : score ATS motivant, export PDF et DOCX natifs,
envoi par e-mail. Trois règles, non négociables :

1. **Le choix, toujours.** À chaque geste, deux voies : « je le fais » ou « Karria le
   fait ». Karria **propose**, la personne **accepte ou refuse**, élément par élément.
2. **Le temps réel.** Chaque modification se voit sur le CV, dans le modèle choisi,
   sans bouton « régénérer ». L'écran, c'est le CV.
3. **Rien qui sente l'IA générique.** Pas de badge « IA », pas d'étincelles, pas de
   violet. Karria parle comme une personne de l'équipe, dans la direction artistique
   du profil : minimaliste, chaleureux, précis.

## Ce qui existe déjà (à réutiliser, pas à refaire)

| Brique | Où | État |
|---|---|---|
| Modèles (Meridian, Minimalist publiés, Marketing en revue) → spécification de mise en page JSON | `modules/documents` | en prod |
| **Vue par rôle** : « quel champ du CV va dans quel emplacement du modèle » (ADR-0020 § 2) | `documents/domain/view.py` | en prod |
| **Intitulé sous le nom, toujours**, synthétisé dans la grammaire du modèle même s'il n'en a pas (ADR-0020 § 3.1) | `documents/domain/headline.py` | en prod |
| Rendu PDF (Gotenberg) et DOCX (python-docx) depuis la vue | `documents/infrastructure` | en prod |
| Porte de fidélité (le rendu doit ressembler au modèle) | `documents/domain/gate.py` | en prod |
| **Score ATS** déterministe (le document est relu comme un ATS) | `documents/domain/ats.py` | en prod |
| Galerie de modèles, aperçu, modèles Premium payés par Stripe | app `features/modeles` | en prod |
| Édition manuelle du CV par formulaires, brouillons, sections « à vérifier » | app `features/cv/components/edition` | en prod |
| Suggestions à la saisie (intitulés, entreprises, villes, écoles, compétences) | `GET /cv/suggestions` | **en cours (2026-11-04)** |
| Assistant Karria (constitution, mémoire, fournisseurs commutables) | `modules/assistant`, `shared/llm` | en prod |

L'atelier est donc surtout une **nouvelle façon de présenter et de piloter** ce qui
existe, plus deux briques neuves : le rendu HTML interactif et l'envoi par e-mail.

## Le parcours

```
Mon CV (données)  →  Créer mon CV  →  Choisir un modèle (avec MES données)
                                   →  L'atelier (temps réel, toucher pour modifier)
                                   →  Finaliser (ATS, PDF, DOCX, e-mail)
```

Et depuis une offre : **« Adapter mon CV à cette offre »** entre directement dans
l'atelier avec les propositions de Karria déjà prêtes à accepter.

## 1. Le rendu temps réel — la décision technique centrale

Trois façons de faire, une recommandée.

| Option | Principe | Pour | Contre |
|---|---|---|---|
| A. Le serveur rend tout | À chaque frappe, l'API rend le HTML du modèle avec le brouillon | fidélité parfaite, une seule logique | 200–400 ms de latence : « temps réel » raté, coûts serveur |
| B. Le client rend tout | Le modèle (HTML + CSS) est embarqué dans l'app et rempli sur place | instantané, hors ligne | la logique de la vue (ADR-0020) serait dupliquée en TypeScript : deux vérités, dérive garantie |
| **C. Hybride (recommandée)** | Le serveur rend une fois le HTML du modèle **avec des ancres** (`data-chemin="experiences.1.missions.2"`) ; l'app l'affiche dans une WebView ; **à chaque frappe, un script local met à jour le texte de l'ancre** (instantané) ; **au repos (400 ms), l'API re-rend** et remplace la page (fidélité, pagination, intitulé synthétisé) | instantané ET fidèle, une seule logique de vue | une WebView à soigner (zoom, pages, mode sombre de l'app autour) |

Concrètement, côté API : `POST /documents/{template_id}/render` reçoit le brouillon
de CV et rend `{ html, pages, warnings }` ; le HTML est celui que Gotenberg
transformera en PDF, **donc ce qu'on voit est ce qu'on exportera**. Côté app : une
page A4 à l'échelle de l'écran, pincer pour zoomer, balayer entre les pages,
l'ombre de la feuille sur le fond de Karria. Sur tablette et en paysage, deux
colonnes : rubriques à gauche, feuille à droite.

## 2. Choisir un modèle, avec ses données

La galerie actuelle montre l'exemple du modèle. Dans le parcours de création, chaque
carte montre **le CV de la personne** dans ce modèle (rendu serveur, mis en cache,
re-rendu quand le CV change). On choisit en voyant le résultat, pas une promesse.
Les modèles Premium restent achetables au même endroit ; un modèle non possédé
se voit en aperçu, s'achète en un geste (Stripe, flux existant), puis s'ouvre.

## 3. L'atelier

**L'écran, c'est la feuille.** Une barre basse, cinq gestes :

- **Toucher un élément** du CV (nom, intitulé, une mission, une compétence) → une
  feuille s'ouvre en bas, avec **le formulaire existant** limité à cet élément,
  avec validations et suggestions. « Enregistrer » → la feuille se ferme, le CV
  est à jour sous les doigts. Toujours deux boutons dans la feuille : « Je
  modifie » (le champ) et « Karria propose » (voir 4).
- **Mise en avant** : ordre des rubriques, masquer ou montrer un élément (une
  expérience ancienne, un centre d'intérêt), choisir ce qui vient en premier,
  réorganiser par glisser. C'est la personne qui décide ce qui est mis en avant.
- **Intitulé** : un champ toujours visible en tête de l'atelier, « Poste visé »,
  avec suggestions ; il est rendu sous le nom dans tous les modèles (déjà fait côté
  serveur). Conseil affiché une fois : un intitulé = le poste que l'on vise,
  formulé comme dans les offres, sans « recherche » ni « candidat ».
- **Modèle** : changer de modèle sans rien perdre.
- **Finaliser** : voir 5.

Annuler / rétablir, enregistrement automatique du brouillon à chaque geste,
reprise là où on s'était arrêté. Rien n'est perdu si l'app se ferme.

## 4. Karria dans l'atelier — propose, n'impose pas

Trois niveaux, tous **réversibles** et **par élément** :

1. **Un élément** : « Reformule cette mission », « Raccourcis ce résumé », « Mets
   des résultats chiffrés si tu en as » → Karria montre **avant / après**, la
   personne accepte, refuse ou modifie la proposition.
2. **Une rubrique** : « Réordonne mes compétences pour ce poste » → une liste de
   changements, chacun avec sa case.
3. **Pour une offre** : depuis la fiche d'une offre, « Adapter mon CV » → Karria
   propose l'intitulé, met en avant les expériences et compétences qui répondent
   à l'offre, réécrit le résumé, et montre la **couverture des mots-clés** de
   l'offre (ce que l'ATS de l'entreprise cherchera). Toutes les propositions
   arrivent **désactivées** ; on les allume une à une, ou « tout accepter ».
   Le CV adapté est **une version** liée à l'offre, le CV principal n'est pas
   touché.

Garde-fous : Karria **n'invente jamais** un fait (pas de diplôme, d'entreprise ni
de chiffre qui n'est pas dans les données) ; chaque proposition cite la donnée
d'origine ; le consentement IA existant s'applique.

## 5. Finaliser

- **Compatibilité ATS** : le score existant, présenté comme un cadran et une phrase
  qui **motive** : « Ton CV est lisible par les logiciels de tri de candidatures
  qu'utilisent la grande majorité des grandes entreprises en France et dans le
  monde. » Le chiffre exact viendra d'une constante serveur **avec sa source**
  (à sourcer avant mise en prod : les études citent ~97 % des Fortune 500 et une
  large majorité des grands groupes français ; on ne publie pas un chiffre non
  sourcé). En dessous, les deux ou trois points qui feraient monter le score, chacun
  avec « Je corrige » / « Karria corrige ».
- **Exporter** : PDF et DOCX **dans Karria** (génération serveur existante, fichier
  enregistré sur le téléphone, feuille de partage du système). Le DOCX garde les
  mêmes polices et la même mise en page que le PDF (vue commune).
- **Envoyer par e-mail** : à mon adresse (celle du compte, pré-remplie) ou à une
  autre adresse saisie. Le PDF en pièce jointe, un court message signé du nom de
  la personne, jamais de texte libre (pour que Karria ne devienne pas un outil
  d'envoi de courrier). Limites : 5 envois par jour et par compte, adresse
  confirmée visuellement avant l'envoi, journal des envois.

## 6. La photo : « je peux la rendre plus pro »

Ce qui existe : dans le parcours de création, Karria propose d'améliorer la photo
(lumière, fond neutre, cadrage), compare l'originale et l'améliorée, et la personne
garde celle qu'elle veut (`POST /cv/photo?enhance`, modèle `gemini-3.1-flash-image`,
règle absolue : **on ne touche jamais à l'identité** — même visage, même âge, même
peau, même coiffure ; seuls la lumière, le fond, le cadrage et la netteté changent).

À ajouter, comme dans la V1 (galerie « mes photos générées » dans le profil) :

- **Depuis la photo de profil et depuis l'atelier** : toucher la photo → « La rendre
  plus pro », avec le même avant/après ; pas seulement au moment de la création.
- **Plusieurs rendus** : trois propositions (studio clair, fond sobre coloré, extérieur
  flou), on choisit ou on relance ; chaque rendu garde l'identité (contrôle
  automatique : le serveur compare les visages avant/après et refuse un rendu qui
  dérive — la V2 a déjà la porte `photo_enhance_refused`).
- **Le prompt**, réécrit et versionné côté serveur (jamais dans l'app), avec les
  consignes des photographes de CV : regard vers l'objectif, épaules droites, lumière
  douce de trois quarts, fond uni désaturé, tenue inchangée, aucune retouche de
  morphologie. Le modèle reste commutable (`GEMINI__IMAGE_MODEL`) : on évaluera le
  meilleur modèle image du moment sur un jeu de vingt photos avant de changer.
- **Galerie** dans le profil : les rendus conservés, la photo du CV choisie d'un geste,
  suppression possible (les images sont des données personnelles : effacées avec le
  compte).

## 7. La publicité native, partout dans le parcours, sans gêner

L'objectif économique est la publicité : chaque écran du parcours CV doit pouvoir
porter une **annonce native** (AdMob, composant `carte-annonce` existant du fil),
qui **compte une impression**, sans jamais gêner le geste en cours. Les abonnés
« sans pub » n'en voient aucune (drapeau existant). Emplacements prévus :

| Moment | Emplacement | Pourquoi ça ne gêne pas |
|---|---|---|
| **Fiche d'une offre** (`detail_offre`, décidé le 2026-11-05) | **au-dessus du pli** : entre les étiquettes (lieu, contrat, salaire…) et la description ; jamais au-dessus de « Adapter mon CV », qui vit dans le pied flottant ; calme tactile **700 ms** au lieu de 2 s (l'arrivée sur la fiche est un toucher), doigt levé exigé, entrée en fondu et description qui glisse | posée en bas, elle n'était comptée que si l'on défilait jusqu'à elle ; en haut, elle est vue à chaque ouverture où AdMob remplit |
| Choix du modèle | une carte annonce dans la galerie, au format d'une carte de modèle, après la 3e | même rythme que les modèles |
| Atelier | **jamais sur la feuille** ; une annonce dans la feuille basse quand elle est ouverte sur une rubrique longue (sous le formulaire), et une pendant que Karria réfléchit (temps d'attente) | la feuille reste pure ; l'attente devient utile |
| Après un enregistrement | une annonce dans la bande « enregistré » qui s'efface d'elle-même | le geste est fini |
| Finaliser | une annonce sous le score ATS, et une pendant la génération du PDF/DOCX | temps d'attente naturel |
| Photo | une annonce pendant la génération des rendus (30–60 s) | temps d'attente naturel |
| Galerie de mes CV / mes photos | une carte annonce dans la liste | même rythme que la liste |

Règles : une seule annonce visible à la fois, jamais au-dessus d'un bouton principal,
jamais dans les 2 secondes qui suivent un toucher (pas de clic accidentel, exigé par
AdMob) — un emplacement peut demander moins (`CALME_PAR_EMPLACEMENT`, 700 ms sur la fiche
d'offre) mais **jamais sous un doigt posé** (la racine note la fin de chaque toucher,
`calme-tactile.ts`) —, chargement en arrière-plan pour ne jamais faire attendre, et
chaque impression est journalisée côté app (compteur par emplacement) pour mesurer ce
que rapporte chaque moment et retirer ceux qui font décrocher.

**Pas de garantie d'impression à chaque ouverture**, et aucune app ne l'a : il faut que
la personne ne soit pas sans pub, que le consentement UMP/ATT soit obtenu, qu'AdMob
remplisse la demande, que l'écran soit encore ouvert au calme tactile, et que la carte
soit **réellement visible** (le SDK n'émet `IMPRESSION` qu'à l'affichage de la
`NativeAdView`). L'app ne maîtrise que les deux derniers — d'où la place au-dessus du pli
et le délai court ; le reste se lit dans la console AdMob (demandes, remplissage).

## Les phases, dans l'ordre

| Phase | Contenu | Livrable visible |
|---|---|---|
| **1. Socle temps réel** | API : rendu HTML avec ancres (`/render`), pagination ; app : feuille A4, zoom, pages, mode sombre autour | je vois mon CV dans un modèle, il bouge quand mes données changent |
| **2. Choix + atelier manuel** | galerie avec mes données ; toucher pour modifier avec les formulaires existants en feuille ; mise en avant ; intitulé en tête ; annuler/rétablir ; brouillon auto | je crée un CV complet sans l'IA |
| **3. Karria dans l'atelier** | propositions avant/après par élément ; par rubrique ; « Adapter à cette offre » avec couverture des mots-clés ; versions liées aux offres | Karria m'aide, je garde la main |
| **4. Finaliser** | ATS motivant avec corrections proposées ; export PDF/DOCX natif ; envoi e-mail avec limites | j'exporte et j'envoie sans quitter Karria |
| **5. Photo pro** | « la rendre plus pro » depuis le profil et l'atelier, trois rendus, contrôle d'identité, galerie | une photo de CV digne d'un studio |
| **6. Publicité native** | les emplacements du § 7, un par phase livrée, compteur d'impressions par emplacement | chaque écran rapporte sans gêner |
| **7. Finitions** | tablette/paysage, accessibilité, performance sur iPhone 13, tests sur appareil, notes de révision Apple | prêt pour la boutique |

La publicité n'attend pas la phase 6 : chaque phase livre **ses** emplacements en même
temps que ses écrans, pour que les impressions commencent dès la phase 2.

Chaque phase se livre en production, testée, avec ses ADR (rendu temps réel ; envoi
d'e-mail ; versions de CV par offre), dans le respect du socle (ports & adapters,
mypy strict, `make check` / `npm run check`).

## Ce que Marc doit trancher

1. **Option C** (hybride) pour le temps réel — ou une autre.
2. **L'envoi par e-mail** : les limites proposées (5/jour, pas de texte libre).
3. **Le chiffre ATS** : on affiche une phrase sans chiffre tant que la source n'est
   pas validée — ou tu valides une source.
4. **Les versions par offre** : conservées combien de temps, visibles où (fiche de
   l'offre, et « Mes CV » ?).
