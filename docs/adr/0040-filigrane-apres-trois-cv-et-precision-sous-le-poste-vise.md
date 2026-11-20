# ADR-0040 — Le filigrane après trois CV par mois, la précision sous le poste visé, le bloc de titre posé comme un tout

**Statut :** Proposée — 2026-11-20 (demandes de Marc du 20/11, avant la campagne sponsorisée ; à valider après essai sur appareil)
**Complète :** ADR-0020 (modèles de CV), ADR-0024 (« Karria sans pub »), ADR-0029 (atelier), ADR-0035 (pied de page), ADR-0037 (ingestion v2), ADR-0038 (quotas d'IA)
**Contexte :** module `documents` (`domain/watermark.py`, `application/allowance.py`, `infrastructure/header_layout.py`), module `cv` (`Identity.availability`), migration 24

## Le problème

1. **Le rendu d'un vrai CV défaisait le dessin des modèles** (revue visuelle des
   neuf modèles publiés, quatre CV d'essai) : prénom et nom dans deux corps ou
   deux graisses, nom long replié sur quatre lignes dans le poste visé, poste
   synthétisé réduit à 5 pt contre la marge, pictogrammes sur le texte, corps
   d'un CV renvoyé en page 2 par des coordonnées de pied de page, frise
   traversant la rubrique suivante, établissement absent.
2. **Rien ne dit ce que la personne cherche ni quand elle est disponible.**
   Marc : « recherche alternance 2 ans », « disponible immédiatement » — une
   ligne sous l'intitulé, quand le modèle le permet.
3. **Le filigrane est partout, toujours**, abonné ou non (ADR-0035). Marc veut
   en faire le levier de l'abonnement, **sans jamais retenir un document**.

## La décision

### 1. Le bloc de titre est posé comme un tout (`header_layout`)

Pour le CV d'une personne — jamais pour l'exemple de galerie — la
spécification passe par `layout_header` avant les deux moteurs (PDF/atelier
et DOCX) :

- prénom et nom prennent **le style du plus grand des deux** et la **même
  réduction** ; empilés ou sur une ligne comme dans le modèle (sur une ligne :
  prénom puis nom) ; alignés sur le bord que leurs lignes partagent ; le bloc
  ne dépasse pas la hauteur qu'il avait, sa dernière ligne de base ne bouge pas ;
- le poste visé s'aligne comme le nom, ne descend pas sous 80 % de son corps
  sur une ligne, se replie sur deux lignes au-delà ; ce qui est posé dessous
  descend d'autant ; la première bande n'est poussée que par ce qui est
  réellement au-dessus d'elle (`data-hb`) ;
- les coordonnées empilées retrouvent leur bord commun ; un alignement que la
  géométrie dément est retiré.

Le dessin d'un modèle qui distinguait le prénom du nom (Minimalist, Croisée
Graphite, Frise Graphite, Meridian) est donc **volontairement unifié** pour un
vrai CV : Marc tient la différence de caractère pour une faute sur un CV.

### 2. La précision : `identity.availability`

Champ facultatif du CV (150 caractères), sans migration (l'identité est un
JSON). Écrite sous le poste visé, dans sa famille, en bas de casse, entre
8,5 et 12 pt, alignée comme lui ; sans poste visé, elle prend sa place. Chemin
d'atelier `identity.availability`. Rien n'est affiché sans elle.

### 3. Le filigrane : trois CV sans, puis avec — jamais de blocage

- Chaque mois civil (UTC), les **trois premiers CV exportés** d'un compte
  gratuit sortent **sans** la ligne « réalisé avec Karria ». Ensuite elle est
  posée, et l'app propose « Karria sans pub » à 4,99 € par mois pour la retirer
  et soutenir Karria. Refuser ne retire rien : le fichier se télécharge.
- Un abonné n'a jamais de filigrane.
- **Une génération n'est pas un fichier** : l'app exporte à l'ouverture de
  « Finaliser », après « Optimiser » et au retour de l'atelier. Les exports sans
  filigrane faits dans la demi-heure qui suit le premier comptent pour **une**
  génération, et restent sans filigrane pendant cette demi-heure.
- Une lettre suit l'état du compte au moment de son export ; elle ne compte pas.
- L'aperçu de l'atelier porte le filigrane que l'export portera.
- `documents_generated_cvs.watermarked` (migration 24) : seuls les CV exportés
  **sans** filigrane depuis le début du mois sont comptés ; les exports
  antérieurs valent `false`.
- La recherche d'offres et l'assistant restent gratuits ; les portraits IA
  gardent leur quota de trois par mois (ADR-0038), et l'app y propose le même
  abonnement.

Contrat : `GeneratedCv.watermarked`, `Rendu.watermark`,
`GET /cv/me/generated-cvs/allowance` → `{subscriber, free_per_month, used,
remaining, watermark_next, resets_at}`.

## Ce qui est écarté

- **Bloquer le téléchargement au-delà de trois** : refusé par Marc, et contraire
  à la promesse du produit.
- **Compter les exports filigranés** : ils ne coûtent rien à la personne, ils
  ne doivent rien entamer.
- **Garder la distinction typographique prénom / nom** des modèles d'origine.
- **Poser la précision dans le poste visé** (une seule ligne plus longue) : elle
  se replie mal et brouille le mot-clé que lit un ATS.

## Conséquences

- Deux exports simultanés peuvent tous deux lire « deux utilisés » : au pire
  un quatrième CV sans filigrane dans le mois. Accepté.
- Vendre le retrait du filigrane dans les apps relève des mêmes règles de
  magasin que « sans pub » (ADR-0039) : achat intégré exigé par Apple et
  Google ; le risque de refus avec Stripe est déjà connu et assumé par Marc.
- Les aperçus de galerie et la porte de fidélité ne changent pas (exemple).
- Reste à faire : le champ et la proposition d'abonnement dans l'app ; un essai
  sur appareil ; l'impression Gotenberg en production.
