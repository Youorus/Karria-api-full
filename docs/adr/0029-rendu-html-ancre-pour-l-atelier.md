# ADR-0029 — Rendu HTML ancré pour l'atelier CV : le même HTML que le PDF, des ancres `data-chemin`, une pagination estimée

**Statut :** Proposée — 2026-11-05 (écrite par Fable, à valider par Marc)
**Complète :** ADR-0020 (modèles de CV et exports)
**Contexte :** `docs/atelier-cv-feuille-de-route.md`, option C (hybride)

## Le problème

L'atelier doit montrer le CV de la personne **dans le modèle choisi, en temps
réel**, à chaque frappe. Rendre côté serveur à chaque touche coûte 200–400 ms
(Gotenberg) ; rendre côté client obligerait à dupliquer en TypeScript la
logique « quel champ du CV va dans quel emplacement » (ADR-0020 § 2) — deux
vérités, dérive garantie.

## La décision

1. **Une route de session**, `POST /api/v1/documents/{template_id}/render`,
   reçoit le CV **tel que l'app le tient** (le brouillon, forme de `GET /cv/me`)
   et rend `{ html, pages, warnings }`. Le HTML est produit par **le même
   moteur** que le PDF (`HtmlRenderer`) : même vue, même intitulé synthétisé,
   mêmes ajustements d'espacement. Ce qu'on voit est ce qu'on exportera.
2. **Chaque texte issu du CV porte `data-chemin`**, le chemin JSON de sa
   donnée (`identity.headline`, `experiences.1.missions.2`, `skills.3`). La
   vue (`domain/view.py`) attache les chemins aux éléments qu'elle produit ;
   le moteur HTML les écrit sans les inventer. Les champs **composés** (nom
   complet, « Poste - Entreprise », dates) portent un chemin **virtuel**
   (`identity.full_name`, `experiences.1`, `experiences.1.dates`) : l'app patche
   le texte à la frappe et sait quel élément a été touché ; au repos, elle
   redemande le rendu et retrouve la vérité.
3. **Autonome et cachable.** La page ne charge rien hors de
   `storage.karria.fr` : les polices sont servies par **URL publique stable**
   depuis un bucket public `assets` (copies des instances statiques libres de
   la bibliothèque, posées à la première demande), les pictogrammes du modèle
   voyagent en `data:` (quelques kilo-octets), la photo par URL signée gardée
   stable le temps de sa validité. Une URL de police qui changerait à chaque
   rendu ferait retélécharger 300 Ko toutes les 400 ms.
4. **`pages` est une estimation par mesure** (métriques réelles des polices,
   mêmes fonctions que le placement), pas un passage par Chromium : le rendu
   doit répondre en quelques dizaines de millisecondes. La vérité est dans le
   HTML lui-même : son script d'ajustement, après chargement des polices, pose
   `window.karriaFit = {k, f, pages}` (et le titre `fit k=… f=… pages=…` que
   lit déjà Gotenberg) — l'app peut la lire dans la WebView. Les pages se
   suivent verticalement (`.page` de hauteur `pages × 297 mm`), un filet
   discret marque chaque coupure.
5. **Bornes.** Corps borné par le schéma (listes plafonnées) ; HTML rendu
   borné à 1,5 Mo (`422 render_too_large`) ; **90 rendus par minute et par
   compte** (`429 render_rate_limited`) — l'app ne rend qu'au repos (400 ms).
   Modèle non possédé : `403 template_not_owned`. L'exemple du modèle
   (`?sample=true`) sert la galerie, sans ancres.
6. **Réutilisable par un autre module.** Le cas d'usage `RenderDocument` est
   exposé par la façade `documents.build_render_document(settings, storage)`
   (hors requête) et `documents.get_render_document` (dépendance FastAPI) ;
   un module qui en a besoin déclare son port et `bootstrap` branche
   (règle 4), comme `offers.build_offer_vocabulary` pour `cv`.

## Conséquences

- Le moteur HTML gagne deux responsabilités orthogonales : un **résolveur
  d'URL** (fichier → adresse) et un mode **ancré**. Le rendu Gotenberg reste
  identique (fichiers joints, pas d'ancres).
- `pages` estimé peut différer d'une page de la vérité sur un CV à la limite ;
  c'est accepté, l'app lit `window.karriaFit` pour l'affichage final.
- Un bucket public de plus (`assets`), provisionné par `make
  storage-provision`. Il ne contient que des polices libres et la marque.
