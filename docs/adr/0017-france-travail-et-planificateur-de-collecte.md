# ADR-0017 — France Travail en couche de base, collecte continue, planificateur piloté par la demande

**Statut :** Proposée — 2026-10-22 (à valider par Marc)
**Complète :** ADR-0016 (moteur d'offres). Ne revient sur aucun de ses choix ;
elle en change la **source principale**, le **rythme** et le **pilotage**.
**Contexte :** composant `karria.collector`, module `offers`

## Le problème

L'ADR-0016 collectait par JobSpy (Indeed, HelloWork) à partir de cibles
dérivées des recherches, un passage toutes les dix minutes. Trois limites,
vues le jour même :

- **la fraîcheur dépendait d'un site tiers** qui change son balisage sans
  prévenir (HelloWork, 9 h 30 : zéro annonce sans erreur) ;
- **la collecte ne commençait qu'après la demande** : une personne qui
  cherche un métier rare attend le passage suivant ;
- **les cibles grandissaient avec les personnes** (deux intitulés par
  personne, une ville par personne) : dix mille personnes auraient fait dix
  mille requêtes par site.

Marc fixe la force de Karria : le **temps réel**. Chaque offre nouvelle
doit atteindre les fils de **toutes** les personnes dont elle remplit les
critères, en quelques minutes, sans qu'aucune ne la demande. Et il fixe une
règle : **une conversation ne déclenche jamais une recherche en direct sur
France Travail, JobSpy ou un site** — elle ne lit que notre index.

Tout ce qui suit a été mesuré le 2026-10-22 sur l'environnement
d'évaluation (`karria_eval`, serveur partagé) avec les identifiants
partenaires de la V1.

## Ce qu'on décide

### 1. France Travail est la source n°1 : officielle, incrémentale, nationale

L'API « Offres d'emploi v2 » (OAuth2 `client_credentials`) devient la couche
de base ; JobSpy ne vaut plus que là où il ajoute (§ 5). Mesuré :

| | valeur |
|---|---|
| Débit accordé | 10 appels/s par client (`X-Ratelimit-*`) ; on s'en tient à 8 |
| Pagination | 150 par page, index final 3 149 (« 3000-3150 » → 400) |
| Volume | **24 119 annonces créées en 24 h** (un jour de semaine) ; ~140/min en journée |
| Lecture d'une journée complète | 317 requêtes, **110 s**, 13 lots de 2 000 ; ingestion à **340-600 annonces/s** quand elles sont déjà connues (le cas du balayage), ~17/s quand elles sont nouvelles (géocodage évité, plongement différé) |
| Détail d'une annonce retirée | `GET /offres/{id}` → 204 |
| Temps de travail renseigné | **53 %** des annonces (`dureeTravailLibelleConverti` vide sur 47 %, surtout intérim et non-salarié ; mesuré sur 900 annonces de la veille et confirmé sur 30 418 en base), contre 71 % sur Indeed et 100 % sur HelloWork |
| Coordonnées | données par l'API : **aucun géocodage** pour cette source |

Deux pièges, consignés dans le code (`collector/france_travail.py`) :

- **le fuseau.** Les instants sont l'heure de **Paris** marquée `Z`
  (mesuré : à 14 h 39 UTC, l'API rendait des annonces créées « 16:39:17Z »
  et une fenêtre `[maintenant − 1 h, maintenant]` en UTC rendait des
  annonces vieilles de deux heures). Les bornes `minCreationDate` et
  `maxCreationDate` sont écrites dans ce fuseau, les instants lus dans ce
  fuseau, et du vrai UTC est remis à l'API ;
- **le plafond de 3 150.** Une fenêtre qui le dépasse se partitionne par
  département (101), puis par moitiés de temps jusqu'à cinq minutes ; au-delà
  on prend ce que l'API donne et on le journalise (`window_truncated`).

**Le contrat de remise** (`docs/collecteur.md`, version 1, additive) porte
désormais des **indices structurés** que la source donne explicitement :
contrat, temps de travail, expérience (ou années), libellé de salaire,
coordonnées, parution à la seconde, et une ligne `expired: true` pour une
annonce retirée. Ils **priment** sur la lecture du texte
(`normalization.py`) ; un indice hors des énumérations du domaine vaut
« absent ». Le ROME voyage dans `raw` seulement — Karria ne classe plus
par ROME (ADR-0016).

### 2. Collecte continue : trois minutes, un recouvrement, un balayage

Le collecteur tourne deux boucles dans le même processus :

- **France Travail**, toutes les 3 minutes : les annonces créées depuis le
  dernier passage, avec 5 minutes de recouvrement (une annonce revue à
  l'identique ne coûte à l'API qu'un `last_seen_at`). Mesuré : 2 requêtes
  et 0,6 s par passage, 60 à 80 annonces en journée ;
- **JobSpy**, toutes les 10 minutes, sur les cibles que le planificateur
  lui confie (§ 5) ;
- un **balayage** quotidien par département de la veille (2 h UTC) rattrape
  ce qu'un passage aurait manqué, et c'est aussi l'amorçage d'un
  environnement (`python -m karria.collector --ft-sweep 1`).

Le collecteur reste **sans état durable** : au redémarrage, il relit deux
heures en arrière. Il ne garde que ses identifiants partenaires et le jeton
de service ; rien de plus (ADR-0016 § 1).

### 3. Objectifs de fraîcheur (SLO) et mesure

Les instants mesurés : parution (`published_at`, à la seconde pour France
Travail) → vue par le collecteur → ingérée (`first_seen_at`) → indexée
(`indexed_at`) → dans un fil (`offers_feed_entries.created_at`, matching
inversé de l'ADR-0016 § 8).

| étape | objectif (p95) | régime courant (270 annonces parues en 30 min), p50 / p95 | sous un balayage de 24 000 annonces (2 774 parues en 45 min), p50 / p95 |
|---|---|---|---|
| parution → ingérée | ≤ 10 min (3 min de cadence + le délai de publication côté France Travail) | 1 min 35 s / **4 min 25 s** | 2 min 34 s / 12 min 56 s |
| ingérée → indexée | ≤ 3 min (outbox vidée chaque minute, lots de 64) | 2 s / **1 min 31 s** | 2 min 01 s / 3 min 46 s (contention sur le serveur d'inférence) |
| parution → indexée | ≤ 12 min | 1 min 49 s / **4 min 47 s** | 4 min 47 s / 14 min 28 s |
| parution → fil (alerte) | **≤ 15 min** | 1 min 32 s en médiane sur 8 alertes récentes (270 alertes pour 23 personnes depuis l'amorçage) ; l'alerte part dans la seconde qui suit l'indexation | — |

En régime courant, tous les objectifs sont tenus avec de la marge ; sous
balayage, « ingérée → indexée » déborde (le balayage et le flux partagent le
serveur d'inférence) et « parution → indexée » frôle les 15 minutes. Le
balayage est donc planifié la nuit (2 h UTC), quand le flux est au plus bas.
Le p95 « parution → ingérée » dit aussi que quelques annonces apparaissent
dans l'API plus tard que leur `dateCreation` : le recouvrement de 5 minutes
les rattrape, le balayage le reste.

La requête de mesure est dans `docs/collecteur.md` ; elle se relit sans
outil. Un dépassement durable est un incident, pas un réglage.

### 4. Temps de travail inconnu : le temps partiel est strict, le temps plein ne l'est pas

Avant : une offre sans temps de travail passait tout filtre. Mesuré :
sur France Travail, parmi les offres qui le disent, **81 % sont à temps
plein** (12 798 contre 2 951 en base) et **47 % ne le disent pas** ;
Indeed laisse le temps inconnu sur 29 % des offres, HelloWork jamais. Décision, symétrique de la règle des contrats stricts
(alternance, stage) :

- « temps partiel » demandé → une offre **muette est écartée** : une annonce
  à temps partiel le dit toujours (c'est son argument, et la durée est une
  mention obligatoire) ; une annonce muette est presque toujours à temps
  plein, montrer quatre offres sur cinq à côté serait du bruit ;
- « temps plein » demandé → une offre muette **passe** : l'absence ne
  prouve rien, et l'écarter retirerait 29 % du corpus Indeed et 47 % du
  corpus France Travail, dont l'écrasante majorité est à temps plein.

Le prix, mesuré : pour « cuisinier en temps plein », la base porte 289
annonces France Travail de la veille marquées temps plein, et 418 en
comptant les muettes ; France Travail en compte 374 pour son propre filtre
`dureeHebdo=1`. Le filtre strict côté « temps partiel » coûte donc du
rappel aux personnes qui le demandent ; c'est le choix.

La règle vit à un seul endroit (`SearchCriteria.strict_schedule`) et le
filtre Qdrant la reflète ; un test les confronte.

### 5. Le planificateur de collecte piloté par la demande (`domain/steering.py`)

**Ce que JobSpy reçoit n'est jamais le texte d'une personne.** Une demande
— même un paragraphe mêlant deux métiers, un rayon, un contrat et dix
préférences — devient :

1. des **critères structurés** (sortie à schéma du modèle, ADR-0013) qui
   portent désormais `search_terms` (1 à 4 mots par métier distinct, au
   plus 3, « tels qu'on les taperait dans la barre de recherche d'un site »)
   et `preferences` (ambiance, valeurs, rythme, mobilité : sert au dialogue
   et au classement, **jamais** à la collecte) ;
2. des **cibles canoniques** `terme plié × département × contrat` : mille
   personnes qui cherchent « serveur à Paris » font **une** cible ; le lien
   personne ↔ cible (`offers_target_demands`) porte la demande et sa
   fraîcheur. Le site reçoit la **ville nommée par le géocodeur** (« Lille »,
   jamais « Lille et alentours »), le plus grand rayon demandé, et le
   contrat en préfixe quand il change la requête (« alternance développeur ») ;
3. une **validation** avant d'exister : forme (1 à 4 mots, 40 caractères,
   pas un mot générique comme « emploi » ou « CDI », pas une phrase), puis
   **fond** : le terme doit passer la porte d'intitulé sur au moins un
   titre réel que l'index lui rapproche (`OfferIndex.title_candidates`,
   branche lexicale d'intitulé). Un terme que l'index rapproche de titres
   qui ne disent pas ce métier est refusé et journalisé
   (`offers.target_term_rejected`) ; un terme que l'index ne connaît pas du
   tout entre **à l'essai**. Un métier trop vague (« quelque chose dans la
   vente ») : le modèle laisse `search_terms` vide et pose une question —
   vérifié sur la demande réelle, aucune cible créée ;
4. une **période d'essai** : le premier passage tranche. Zéro annonce, ou
   moins de 30 % des titres qui passent la porte d'intitulé du terme → la
   cible est **rétrogradée** (cadence ×4, `offers.target_demoted`) ; la
   réécriture par le modèle est un pas suivant (`PLUS-TARD.md`).

**La priorité** d'une cible échue (`priority`) :

```
log(1 + demande) + 2·manque + rendement + ancienneté − redondance
```

- *demande* : somme des personnes, chacune pesée par la fraîcheur de sa
  recherche (demi-vie 14 jours) — mille personnes valent plus qu'une, pas
  mille fois plus ;
- *manque* : une recherche qui a rendu moins de 10 offres dans les 24 h
  (le signal le plus urgent : la personne attend) ;
- *rendement* : offres nouvelles par passage, lissées (EMA 0,3), bornées à 20 ;
- *ancienneté* : temps écoulé rapporté à la cadence due, borné à 2 ;
- *redondance* : part des annonces du passage qui doublonnaient une offre
  France Travail, au-delà de 80 % (une cible que la couche de base couvre
  déjà ne mérite pas une requête de site).

**La cadence** s'adapte (`interval_for`) : base 6 h (demande) ou 24 h
(socle), raccourcie jusqu'à **1 h** par le rendement, **doublée** à chaque
passage sans rien de nouveau (bornée à 7 jours), ×4 pour une cible
rétrogradée ; une cible rétrogradée qui rapporte redevient active. C'est un
bandit à exploration/exploitation sans en porter le nom : ce qui rapporte
est repoll é, ce qui se tait recule.

**La politesse** vit dans le collecteur : pause de 2 s à ±30 % de gigue (un
rythme parfait est une signature de robot), **budget horaire par site**
(200 requêtes glissantes), disjoncteur après 3 échecs, et un **zéro
silencieux** : un site qui rend zéro sur trois cibles qui rendaient hier
(`last_found ≥ 5`) est arrêté pour le passage et journalisé en erreur —
c'est le HelloWork du 22 octobre, détecté au lieu d'être subi. Le
rendement anormal côté API (`offers.target_yield_anomaly`) dit la même
chose vue de l'ingestion.

### 6. Une conversation ne cherche jamais en direct

Règle posée par Marc, encodée trois fois : le contrat import-linter
« Collecteur — l'API ne l'importe jamais » (ni `karria.collector`, ni
`jobspy`) ; le test `tests/unit/offers/test_no_live_search.py` (aucun
import de JobSpy ni URL de France Travail ou d'un site dans `offers`, et la
recherche ne dépend que de l'index, du plongement, de la base et des
cibles) ; et la forme des cas d'usage, qui n'ont pas de port vers une
source. Une recherche maigre fait une seule chose : elle rend ses cibles
**dues** (`thin_since`), et le collecteur les prend à son passage.
Personne n'attend un site.

## Mesures

### Évaluation de cohérence (36 demandes réelles, index de 31 725 offres canoniques)

Index au moment de l'évaluation : 21 189 France Travail (un jour), 5 494
Indeed, 5 042 HelloWork (trois jours). Recherche : **p50 65 ms, p95 98 ms**
(77 ms sur 11 581 offres la veille : l'index a triplé, la latence a pris
20 ms). Dialogue : p50 4,3 s, p95 5,2 s (inchangé).

Précision@10 **jugée par un modèle** (Gemini, température 0, qui reçoit la
demande, les critères compris — lieu, rayon, départements, contrats — et
les dix offres ; 287 offres jugées) :

| source | pertinentes / jugées | précision@10 |
|---|---|---|
| France Travail | 85 / 102 | **83,3 %** |
| HelloWork | 81 / 95 | **85,3 %** |
| Indeed | 70 / 90 | **77,8 %** |
| toutes | 236 / 287 | **82,2 %** |

Le même juge **sans** les critères compris (il ignorait le rayon et jugeait
une offre muette sur « de nuit » comme contraire) donnait 58,3 % : la
différence est entièrement dans la consigne du juge, pas dans le moteur.
Les 51 refus restants sont, dans l'ordre : un métier voisin que la porte
d'intitulé laisse passer par inclusion (« Chef cuisinier » pour
« cuisinier », « Aide ménagère » pour « auxiliaire de vie », « Assistant
administratif et commercial » pour « assistante administrative »), et une
spécialité (« Data analyst » pour « data science »).

Le « 95,6 % jugé à la main » de l'ADR-0016 n'est pas comparable : il
jugeait le métier et la zone, pas le niveau (« chef cuisinier » pour
« cuisinier ») ni le temps de travail. **Le défaut réel que le juge
révèle** : la porte d'intitulé accepte un titre qui *contient* l'intitulé
demandé ; « Chef cuisinier / Cheffe cuisinière » passe pour « cuisinier »
(4 des 10 premières de la requête nationale). C'est une limite connue de
l'ADR-0016 § 6 (porte par inclusion de mots) ; la corriger — refuser un mot
de **niveau** (« chef », « responsable », « directeur », « aide », « assistant »)
absent de la demande — est le premier pas suivant.

### « cuisinier en temps plein dans toute la France »

Compris : `job_titles` [cuisinier, cuisinière, commis de cuisine, chef de
partie, second de cuisine], `search_terms` [cuisinier], `whole_france`,
`schedule = temps_plein`. 300 candidats, 150 retenus (le plafond du fil).
Les dix premières : 9 France Travail, 1 Indeed ; **10/10 en CDI à temps
plein**, dix communes de dix départements (57, 74, 31, 57, 54, 27, 01, 13,
78, 06) — le filtre de temps et la couverture nationale font ce qu'on
attend ; **7/10 jugées pertinentes**, les trois refus sont « Chef
cuisinier » (×2) et « Cuisinier chef de partie » (le défaut ci-dessus).
Référence France Travail pour les mêmes filtres : 5 045 offres actives,
374 créées la veille. Le détail des dix est dans le rapport de session.

### Rappel estimé (France Travail, annonces créées la veille)

Comparaison entre le compte France Travail (`publieeDepuis=1`, mêmes
filtres) et ce que notre base porte de cette source sur la même journée,
par une requête SQL approchante (titre, département, contrat). À lire comme
un **ordre de grandeur** : `motsCles` cherche aussi dans la description, nos
`ilike` ne lisent que le titre.

| filtre | France Travail (veille) | notre base (source FT, veille) | lecture |
|---|---|---|---|
| toutes les annonces de la veille | 24 159 lues par le balayage | **25 575** | l'ingestion est complète (les passages de 3 min s'ajoutent au balayage) |
| cuisinier, temps plein | 374 | 289 (418 en comptant le temps inconnu) | 77 % : l'écart est le temps de travail que la source ne dit pas |
| infirmier, CDI, Haute-Garonne | 30 | 25 | 83 % |
| comptable, CDI, Gironde | 54 | 34 | 63 % : `motsCles` lit la description, nous le titre |
| serveur, Paris (75) | 23 | 8 | idem, et l'intitulé France Travail est souvent « Employé de restauration » |
| électricien, Ille-et-Vilaine | 37 | 11 | idem |
| cariste, Haute-Garonne | 7 | 1 | idem |

**Le rappel d'ingestion est complet** ; le rappel par filtre dépend de ce
que le titre dit (la porte d'intitulé est volontairement stricte) et de ce
que la source renseigne. La première lecture, avant correction du fuseau,
donnait 230/374 pour « cuisinier » : le balayage lu en UTC ratait les cinq
dernières heures de la journée.

### Dédoublonnement inter-sources

Sur 30 244 annonces France Travail reçues : 3 308 doublons, dont **3 265
France Travail ↔ France Travail** (la même offre republiée sous un autre
identifiant), 41 avec Indeed, 2 avec HelloWork. 517 annonces France Travail
(1,7 %) pointent un lien de candidature Indeed : ce sont les reprises de
partenaires que le condensat et le SimHash ne rapprochent pas (texte
reformaté). **Décision : pas de dédoublonnement par URL maintenant** —
1,7 % du flux, pour un index sur les URL et une normalisation de liens de
suivi ; consigné dans `PLUS-TARD.md` avec le chiffre, à reconsidérer si la
part dépasse 5 %.

### Les demandes longues et touffues (12 demandes réelles, § 5)

Douze demandes de plusieurs phrases, mêlant métiers, lieu, contrat et
préférences, passées telles quelles (`scripts/offers_demand.py complex`).
Jugement à la main :

| demande | critères | termes de collecte | verdict |
|---|---|---|---|
| serveuse 6 ans, service ou barmaid, Lyon à vélo, pas de temps partiel, équipe sympa | temps plein, Lyon 10 km, confirmé, préférences [pas de coupures, équipe sympa, vélo, management] | serveuse, barmaid | juste |
| BTS compta, alternance DCG, Bordeaux, pas de permis | alternance, Bordeaux 15 km, débutant, préférences [tram] | alternance comptabilité → **comptabilité**, apprenti comptable | juste après correction (le contrat était répété dans le terme : corrigé, `clean_term` retire « alternance / stage » en tête) |
| dev Python/Django 5 ans, full remote, startup, 50 k | télétravail+hybride, Nantes 30 km (le télétravail complet compte partout), confirmé, 4 166 €/mois, préférences [PME, pas d'ESN] | développeur python django, développeur python | juste |
| aide-soignante nuit → jour, CDI, Toulouse, public | *clarify* : le modèle n'avait rempli que `search_terms` | — | **défaut corrigé** : `job_titles` se déduit de `search_terms` |
| cariste CACES, intérim/CDD, Lille 20 km, journée, prépa de commandes | intérim+CDD, Lille 20 km, préférences [journée, pas de nuit] | cariste, préparateur de commandes | juste ; le site reçoit désormais « Lille », plus « Lille et alentours » |
| responsable de rayon → chef de secteur, IDF, pas de discount | départements 75-95, confirmé, préférences [évolution, management, pas de discount] | chef de secteur, directeur adjoint magasin | juste |
| « quelque chose dans la vente, pas trop stressant, Marseille » | **question posée**, aucune cible | — | exactement le comportement voulu |
| infirmière libérale → salariat, CDI clinique, Montpellier ou Nîmes, 2 600 brut | CDI, Montpellier 50 km (Nîmes dans le rayon), senior, 2 600 € | infirmière, cadre de santé | juste |
| étudiant L3, stage data/dev ou job étudiant resto, Grenoble | stage+CDD+intérim+saisonnier, Grenoble, débutant | stagiaire informatique, serveur, employé polyvalent | acceptable ; « stagiaire informatique » sera tranché par l'essai |
| électricien → technicien de maintenance, Rennes, CDI, pas d'intérim | CDI, Rennes 30 km, senior, préférences [site fixe, astreintes payées] | technicien de maintenance, technicien maintenance tertiaire | juste |
| comm/événementiel, Paris, CDI/CDD long, sens, 2 j de télétravail | CDI+CDD, 75, hybride, préférences [ESS/culture, 2 jours] | chargé de communication, chef de projet événementiel | juste |
| cuisinier temps plein toute la France | temps plein, France | cuisinier | juste |

Dix justes, une acceptable, une qui a révélé un défaut corrigé. Aucune
préférence n'est devenue un terme de collecte ; aucune phrase n'est partie
vers un site.

### Le catalogue et l'ordre du jour (36 demandes réelles)

Après `scripts/offers_demand.py seed` : **46 cibles demandées** (1,35 par
demande cherchable, avant regroupement) + 30 du socle, toutes à l'essai.
L'ordre du jour (`plan`) place en tête les cibles des recherches maigres
(« aide cuisinier, Bordeaux », « barmaid, Toulouse », « vendeuse
boulangerie, Nantes »…), priorité 4,69 contre 2,69 pour une demande sans
manque et 0,22 pour une cible du socle non échue.

Le collecteur JobSpy a pris 30 cibles au passage suivant (975 annonces,
aucun site en panne, aucun dépassement de budget). L'essai a tranché :
21 actives, 9 rétrogradées. Parmi les rétrogradées : « stage webmarketing,
Lyon » (0 annonce), « barmaid, Toulouse » (3 annonces, aucune titrée
barmaid), « chef de projet digital, Lyon » (50 annonces, 2 % titrées ainsi)
— justes. Mais aussi « caissière, Lille » : 49 annonces, **0 %** à la
porte, parce qu'Indeed titre « Hôte de caisse » — exactement ce que la
personne cherche. La règle a donc été corrigée le jour même : une cible
dont les titres ne passent pas la porte n'est rétrogradée que si elle
rapporte **moins de 5 offres nouvelles** ; sinon elle reste active et le
désaccord est journalisé (`offers.target_offtarget`) — c'est le signal
de la boucle des synonymes.

### Ce que ça fait à l'échelle

Le nombre de cibles croît avec le nombre de **couples (métier, département)
demandés**, pas avec le nombre de personnes. Modèle, à vérifier sur une
semaine de production :

- 1,35 terme par personne, un département par personne ;
- la demande se concentre : 80 % des personnes sur 20 % des ~400 métiers
  courants, 101 départements, soit ~8 000 cellules fréquentes et ~40 000 au
  total ; le nombre de cibles distinctes suit `N·(1 − e^(−d/N))` ;
- une cible active tourne 4 fois par jour en moyenne (entre 1 h et 24 h
  selon le rendement), une cible rétrogradée 0,5 fois ; deux sites.

| personnes | cibles demandées (distinctes) | requêtes JobSpy / jour | France Travail / jour |
|---|---|---|---|
| 1 000 | ~1 200 | ~7 000 | 480 passages × 2 ≈ 1 000, + 320 (balayage) |
| 10 000 | ~6 500 | ~40 000 | idem |
| 100 000 | ~25 000 (saturation des cellules) | ~150 000, bornés par les budgets de site (200/h/site ≈ 9 600/jour) | idem |

Au-delà de 10 000 personnes, **le budget par site est la vraie borne** :
le planificateur ne fait plus tout tourner, il choisit par priorité — c'est
son rôle. France Travail, lui, ne dépend pas du nombre de personnes.

## Conséquences

- Variables du collecteur : `COLLECTOR__FRANCE_TRAVAIL_*` (identifiants,
  portée, URL, cadence, recouvrement, balayage) ; voir `.env.example` et
  `docs/DEPLOIEMENT.md`. Sans identifiants, le collecteur l'annonce et ne
  collecte que par JobSpy.
- Migration `4_…_planificateur_de_collecte` : colonnes d'apprentissage sur
  `offers_scrape_targets`, table `offers_target_demands`. Les cibles déjà
  collectées passent `active` ; les nouvelles naissent `probation`.
- Le contrat de remise reste en version 1 (ajouts seulement) ; les deux
  rives sont testées ensemble (`tests/unit/test_collector_contract.py`).
- `scripts/offers_demand.py` : `seed` (évaluation seulement), `complex`,
  `catalogue`, `plan`.
- Ce qui reste ouvert, dans `PLUS-TARD.md` : la vérification de vie des
  annonces France Travail par `GET /offres/{id}` (la ligne `expired` est
  prête côté API, la boucle de vérification n'est pas écrite) ; la
  réécriture par le modèle d'une cible rétrogradée ; la boucle de retour
  (ouvertures, sauvegardes, rejets de la porte → synonymes) dont seuls les
  compteurs existent (`gate_rejects`) ; le dédoublonnement par URL de
  partenaire ; la porte d'intitulé et les mots de niveau.

## Alternatives écartées

- **Interroger France Travail à la demande, dans la conversation** — le
  temps de réponse d'un tiers dans le chemin de la personne, une dépendance
  de disponibilité, et aucune alerte possible : la collecte continue sert
  les deux.
- **Une cible par ville** — Lyon et Villeurbanne feraient deux requêtes
  pour les mêmes offres ; le département avec le rayon le plus large couvre
  les deux. Prix : une cible de département est servie depuis la ville la
  plus demandée ; une personne à l'autre bout du département n'a que France
  Travail et le rayon pour elle.
- **Un ordonnanceur à part (cron, Airflow)** — le calendrier est une
  propriété de chaque cible (`next_scrape_at`, appris du rendement), pas une
  table d'horaires ; une boucle et une table suffisent.
- **Dédoublonner par URL de partenaire maintenant** — 1,7 % du flux (voir
  § Mesures).
