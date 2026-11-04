# ADR-0033 — Dossiers de candidature : copie figée du CV, propositions réversibles, lettre liée à une offre, offre figée, purge

**Statut :** Proposée — 2026-11-05 (écrite par Fable, à valider par Marc)
**Complète :** ADR-0014 (persistance du CV), ADR-0018 (couche LLM), ADR-0025
(consentement à l'IA), ADR-0029 (rendu HTML ancré), ADR-0030 (la lettre comme
document), ADR-0015 (suppression de compte)

## Le problème

Depuis la fiche d'une offre, « Adapter mon CV à cette offre » (feuille de
route de l'atelier CV, § 3-5). Il faut une version du CV **liée à l'offre**,
adaptée d'abord par Karria puis retouchée par la personne, une lettre de
motivation, et, à la fin, « as-tu candidaté ? » qui range le tout dans
l'espace Candidatures avec sa trace (offre, date, documents). Trois
contraintes non négociables : la personne a toujours le choix (chaque geste
de Karria s'accepte, se refuse, se défait) ; Karria n'invente jamais un fait ;
le CV de base n'est pas touché.

## La décision

1. **Un module `applications`**, un contexte métier : le dossier de
   candidature. Une table `applications_dossiers`, un dossier par (compte,
   offre), tout en JSONB — comme le CV (ADR-0014) : un dossier se lit et
   s'écrit entier.
2. **La copie du CV est figée.** Le dossier naît d'une copie du CV de base
   (`cv`), avec l'empreinte de l'original (`cv_base_hash`). Le CV de base
   reste intact ; le dossier ne le suit pas, il dit seulement `base_changed`
   quand il a bougé. Ce qu'on y gagne : une candidature envoyée reste ce
   qu'elle était ; ce que ça coûte : une correction du CV de base ne se
   propage pas (la personne la refait, ou repart du CV de base en supprimant
   le dossier).
3. **Karria propose, la personne décide.** L'adaptation produit des
   **propositions** sur des chemins du CV (`identity.headline`, `summary`,
   `experiences.1.missions.0`, `skills` pour l'ordre…), chacune avec `before`,
   `after`, une raison, un état `proposee | acceptee | refusee`. Un chemin
   désigne toujours un emplacement qui existe : une proposition reformule ou
   réordonne, elle n'ajoute jamais un élément. Par défaut Karria **applique**
   l'intitulé et le « à propos » en les marquant `acceptee`, `before`
   conservé : la personne voit « mis à jour » et revient d'un geste ; le reste
   attend. Garde mécanique de « rien d'inventé » : un texte proposé qui
   introduit un **nombre** absent du CV est écarté avant même d'être proposé ;
   les index hors liste et les ordres qui ne sont pas des permutations aussi.
4. **L'adaptation est un état lisible** : `en_cours | faite | echouee`. Elle
   court dans la requête de création, bornée à dix secondes ; au-delà, elle
   part sur la file durable (`applications.adapt_cv`, un verrou par dossier)
   et l'app relit le dossier. Un échec du modèle laisse le dossier utilisable
   à la main ; `POST /{id}/adapt` relance. Une retouche faite pendant
   l'adaptation n'est pas écrasée : les propositions se rejouent sur l'état
   courant.
5. **La lettre n'existe que liée à une offre**, donc dans un dossier.
   Karria écrit la première ébauche (quatre paragraphes typés, bonnes
   pratiques des recruteurs en France, aucun fait hors du CV, aucune
   flatterie) ; la personne édite tout, ou fait réécrire un seul paragraphe
   avec une consigne courte — traitée comme une donnée. Sa forme est la même
   que celle que `documents` met en page (ADR-0030).
6. **L'offre est figée** (`offer_snapshot` : titre, entreprise, condensat du
   logo, lieu, contrat, lien, texte, **mots-clés**). L'offre peut quitter
   l'index ; le dossier reste lisible et l'espace Candidatures garde sa trace.
   Le logo est stocké par condensat, l'URL calculée à la lecture (§ 6.12).
7. **La couverture des mots-clés** (présents / absents dans le CV adapté) est
   une règle du module, sans modèle de langage, recalculée à chaque
   modification. Ce n'est pas le score ATS de `documents` ; c'est
   l'indicateur de l'atelier.
8. **Les autres modules par ports** (règle 4), branchés dans
   `bootstrap/wiring.py` : `OfferReader` et `LogoUrl` ← `offers`,
   `BaseCvReader` et `CvValidator` ← `cv` (une seule définition du CV),
   `TemplateChecker` ← `documents.TemplateShop` (possédé ou gratuit). Le
   rendu, les exports et l'envoi restent dans `documents` : l'app lui passe
   le CV du dossier et `dossier_id`. « J'ai candidaté » publie
   `DossierApplied`, que `bootstrap` relaie au fil d'offres (`applied`).
9. **Purge** : `AccountDeleted` efface les dossiers (rien que des lignes :
   aucun fichier n'appartient au module). Un consentement à l'IA est exigé
   pour créer, relancer, ébaucher, réécrire ; l'usage `application` s'ajoute
   à l'information donnée (ADR-0025) — la version change, l'app redemande.

## Ce qu'on a écarté

- **Modifier le CV de base en place avec un historique** : la personne
  perdrait la version envoyée à telle entreprise, et l'atelier devrait
  défaire des changements entrelacés ; la copie est plus simple et plus vraie.
- **Appliquer toutes les propositions d'un coup** : contraire à la règle du
  choix ; on applique seulement l'intitulé et le résumé, parce que c'est
  l'attente explicite du geste « adapter », et chacun reste réversible.
- **Un dossier par (compte, offre, modèle)** : un modèle se change dans
  l'atelier sans rien perdre ; plusieurs dossiers pour une même offre
  mélangeraient les traces de candidature.
- **Une lettre sans offre** (« lettre générique ») : sans destinataire ni
  exigences, Karria n'écrirait que du vide ; la lettre reste un document
  d'un dossier.
- **Laisser le modèle juger la véracité de ses reformulations** : il ne
  peut pas ; on borne ce qu'on sait borner (chiffres, index, permutations) et
  tout le reste est une proposition que la personne lit.

## Conséquences

- Migration 16 (`applications_dossiers`), à appliquer avant le code.
- Deux routes LLM commutables : `LLM__TASKS__CV_ADAPT`, `LLM__TASKS__LETTER_DRAFT`
  (par défaut Gemini, comme les autres).
- Le consentement à l'IA est à redonner par tout le monde (nouvel usage).
- Contrat pour l'app : `docs/contrat-api-candidatures.md`.
