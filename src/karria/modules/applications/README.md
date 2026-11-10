# Module `applications` — les dossiers de candidature

## Ce qu'il possède

- **Le dossier de candidature** (`applications_dossiers`, un par compte et par
  offre, tout en JSONB) : l'offre figée à l'ouverture (`offer_snapshot`, avec le
  condensat du logo et les mots-clés), la **copie** du CV que Karria adapte puis
  que la personne retouche, l'empreinte du CV de base copié (`cv_base_hash`),
  les propositions de Karria avec leur état (`proposee | acceptee | refusee`),
  la lettre de motivation, la couverture des mots-clés, l'état de l'adaptation
  (`en_cours | faite | echouee`), « j'ai candidaté » (`applied_at`).
- **Les règles** : un chemin de proposition désigne un emplacement qui existe
  (jamais un ajout) ; un texte proposé qui introduit un nombre absent du CV est
  écarté ; l'intitulé et le « à propos » sont appliqués d'office et réversibles,
  le reste attend ; le statut se déduit (`brouillon | pret | candidate`) ; la
  lettre a une forme bornée (1 à 6 paragraphes typés) ; la couverture est une
  règle sans modèle de langage.
- **Les deux appels au modèle** (`cv_adapt`, `letter_draft`), leurs consignes
  et leurs schémas de sortie, et le travail de file `applications.adapt_cv`.

## Ce qu'il ne possède pas, et où ça vit

- **Le CV de base** : module `cv`, lu par le port `BaseCvReader` et jugé par
  `CvValidator` (une seule définition du CV), branchés dans
  `bootstrap/wiring.py`. Jamais modifié d'ici.
- **L'offre et son logo** : module `offers`, par `OfferReader` et `LogoUrl` ;
  « j'ai candidaté » remonte au fil par l'événement `DossierApplied`.
- **Le modèle de CV** : module `documents`, par `TemplateChecker` (possédé ou
  gratuit). Le **rendu, les exports et l'envoi** restent dans `documents` :
  l'app lui passe le `cv` du dossier et `dossier_id`.
- **Le compte et le consentement à l'IA** : `identity` ; la porte du socle
  (`AiConsentedPrincipalDep`) garde les routes qui appellent un modèle.
- **La photo** : celle du CV de base ; le CV d'un dossier n'en porte pas.

## Les choix qu'on ne devine pas en lisant le code

- **Synchrone, puis différé.** L'adaptation court dans la requête de création
  pendant dix secondes au plus (`ADAPTATION_INLINE_SECONDS`) ; au-delà elle
  continue sur la file avec un verrou par dossier, et l'app relit l'état. Le
  même `AdaptCv` sert les deux chemins. Une retouche faite entre-temps n'est pas
  écrasée : les propositions se rejouent sur l'état courant.
- **Un dossier par offre.** Redemander rend l'existant (`200`), sans relancer.
  Repartir du CV de base = supprimer, puis recréer.
- **Le modèle de CV se change à tout moment** (`PUT /{id}/template`,
  `ChangeDossierTemplate`), même pendant l'adaptation, sous la même porte qu'à
  l'ouverture (`TemplateChecker`) ; rien d'autre ne bouge que `template_id` et
  `updated_at` — la mise en page n'est pas le fond (ADR-0033 § 2026-11-05).
- **La couleur principale se change à tout moment** (`PUT /{id}/color`,
  `ChangeDossierColor`, ADR-0034) : `#RRGGBB` normalisé par le schéma, `null`
  rend la main à la couleur du modèle ; elle survit au changement de modèle
  (un choix de la personne, pas un attribut du modèle). Pas de porte : aucune
  règle d'un autre module.
- **Le style de lettre se change à tout moment** (`PUT /{id}/letter-style`,
  `ChangeDossierLetterStyle`, ADR-0036) : `template` (la lettre assortie du
  modèle) ou `plain` (la lettre sobre), `null` = par défaut. Le dossier retient
  le choix, rien de plus : c'est `documents` qui résout (un modèle sans lettre
  assortie sert la sobre) et dit le style servi au rendu et à l'export.
- **`updated_at` est celui du domaine**, pas celui de l'ORM : le dépôt écrit
  par `update()` et remet les horodatages après `create()`. Le cas d'usage s'en
  sert pour savoir si la personne a touché le dossier pendant l'adaptation.
- **Le logo est un condensat**, jamais une URL en base (AGENTS.md § 6.12) ;
  l'URL est calculée à la lecture par le port `LogoUrl`.
- **Les écritures ne renaissent pas et n'écrasent pas.** `insert` à l'ouverture,
  `update` seul ensuite (zéro ligne = dossier supprimé → `dossier_not_found`) ;
  autour de chaque appel au modèle, un contrôle optimiste sur `updated_at`
  (`save_if_unchanged`) : l'adaptation se rejoue sur l'état courant, la lettre
  répond `dossier_conflict`.
- **La sortie du modèle reste une donnée** : blocs offre / CV / lettre / consigne
  encodés en JSON dans les prompts (`<` et `>` échappés), bornes par chemin
  (`domain/bounds.py`) et `CvValidator` avant toute écriture d'une proposition ;
  ce qui ne tient pas reste `proposee`.
- **Garde-fous HTTP** : 30 appels au modèle par compte et par 10 minutes
  (`429 ai_rate_limited`), corps de `PUT /cv` borné à 256 Kio (`422 cv_too_large`),
  plafonds des listes du CV par les chapitres de `cv` (et 30 expériences,
  20 formations, 20 projets).
- **Les ports vers les autres modules refusent par défaut** (`offer_not_found`,
  `cv_missing`, 503 à la retouche, `template_locked`) : un branchement oublié se
  voit.
- **Nouvel usage d'IA** (`application`) : la version du consentement change,
  l'app redemande une fois (ADR-0025).

Décisions : ADR-0033. Contrat : `docs/contrat-api-candidatures.md`.
