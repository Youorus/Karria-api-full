# Contrat API — l'atelier CV : rendu ancré, lettres, envoi par e-mail, version de l'app

Ce que `karria_api_v2` sert pour l'atelier CV (`docs/atelier-cv-feuille-de-route.md`,
phases 1 et 4) : le **rendu HTML ancré** d'un CV ou d'une lettre dans un modèle
(ADR-0029), la **lettre de motivation** comme document exporté (ADR-0030), **l'envoi
par e-mail** des exports (ADR-0031), et la **version minimale de l'app** (module `app`).
Préfixe `/api/v1`, erreurs au format `{ error: { code, message, details: { reason, … } } }`.
Session (`Authorization: Bearer`) exigée partout **sauf** `GET /app/release`.

État : **servi en production** depuis le 2026-11-05 (commits de la session, migration 17).

---

## 1. `POST /documents/{template_id}/render` → `200 Rendu`

Le HTML **exactement** celui que Gotenberg imprimera en PDF (même vue, même intitulé
synthétisé sous le nom, mêmes ajustements), avec sur chaque texte issu du CV un attribut
`data-chemin`. L'app l'affiche dans une WebView ; à la frappe, elle patche localement le
texte de l'élément dont le `data-chemin` correspond ; **au repos (≈ 400 ms)**, elle
redemande le rendu. Aucun fichier n'est écrit.

### Corps

```json
{
  "cv": { …le CV au format de GET /cv/me, tel que l'app tient le brouillon… },
  "letter": null,
  "ats_safe": false,
  "primary_color": "#7B1E2B"
}
```

- `cv` **ou** `letter` (voir § 2 pour la lettre) ; la lettre prime si les deux sont là.
- Bornes du CV (sinon `422` de validation) : 30 expériences, 20 formations, 60 compétences,
  15 langues, 20 centres d'intérêt, 20 projets, 20 missions par expérience, résumé et
  descriptions ≤ 3 000 caractères. Les champs inconnus sont tolérés (le brouillon évolue).
- `?sample=true` (sans corps) : **l'exemple du modèle**, pour la galerie — sans ancres,
  permis même sur un modèle Premium non possédé.
- `ats_safe: true` : la variante « optimisée ATS » (interlettrage plafonné), comme à l'export.
- `primary_color` (ADR-0034, **2026-11-05**) : `#rrggbb` (ou `rrggbb`, `#rgb`), la couleur
  principale choisie par la personne, à la place de celle du modèle (`primary_color` de la
  galerie, celle des titres de rubrique) : toute occurrence de cette couleur — titres, puces,
  filets, bandes — et ses teintes dérivées (une bande pâle reste pâle) ; le texte noir et les
  gris ne bougent pas. Appliquée après `ats_safe`, à l'exemple comme au CV ou à la lettre.
  `null` ou absent : la couleur du modèle. `422` (erreur de validation) sur un autre code.
  Les pictogrammes de contact rastérisés gardent leur couleur (limite connue).

### Réponse

```json
{
  "html": "<!doctype html>…",
  "pages": 1,
  "warnings": ["headline_missing"]
}
```

- `html` : autonome. Polices par URL **publique et stable**
  (`https://storage.karria.fr/karria-production-assets/fonts/…`, cache long),
  pictogrammes du modèle en `data:`, photo de la personne par URL signée gardée stable
  45 min. Rien hors de `storage.karria.fr`. Taille ≤ 1,5 Mo (`422 render_too_large`).
- `pages` : **estimation** par mesure (métriques réelles des polices), sans navigateur.
  La vérité est dans la page : après `document.fonts.ready`, son script pose
  `window.karriaFit = { k, f, pages }` et `document.title = "fit k=… f=… pages=…"`.
  La feuille `.page` prend `pages × 297 mm` de haut, un filet pointillé marque chaque
  coupure (dans l'atelier seulement, jamais à l'impression).
- `warnings` (CV) : `headline_missing` (pas de poste visé), `photo_missing` (le modèle a
  un emplacement, la personne n'a pas de photo), `more_than_one_page`.
  (Lettre) : `logo_unavailable`, `too_few_paragraphs` (< 2), `too_many_paragraphs` (> 5),
  `body_too_long` (> 3 200 signes), `subject_not_explicit` (l'objet ne commence pas par
  « Candidature »), `closing_missing`, `company_missing`.

### Les chemins `data-chemin`

Chemins JSON du CV reçu. Champs **composés** : chemin virtuel, l'app patche le texte à la
frappe et retrouve la vérité au prochain rendu.

| Élément | `data-chemin` |
|---|---|
| nom complet (en-tête) | `identity.full_name` (virtuel : prénom + nom) — ou `identity.first_name` / `identity.last_name` si le modèle les sépare |
| poste visé | `identity.headline` |
| téléphone, e-mail, ville, site | `contact.phone`, `contact.email`, `contact.city`, `contact.links.0.url` (affiché raccourci) |
| résumé | `summary` |
| expérience i : poste, entreprise | `experiences.i.position`, `experiences.i.company` — ou `experiences.i` (virtuel) quand le modèle écrit « Poste - Entreprise » sur une ligne |
| dates, lieu, description | `experiences.i.dates` (virtuel), `experiences.i.location`, `experiences.i.description` |
| mission j | `experiences.i.missions.j` |
| formation i | `educations.i.degree`, `educations.i.school`, `educations.i.dates`, `educations.i.location`, `educations.i.description` |
| compétence, langue, intérêt | `skills.i`, `languages.i`, `interests.i` (index dans la liste du CV, même regroupés par catégorie) |
| projet i | `projects.i.title`, `projects.i.technologies` (virtuel), `projects.i.description` |

Les titres de rubrique (« Expériences professionnelles ») n'ont pas de chemin : ce n'est
pas une donnée du CV. Une ligne qui réunit plusieurs champs (« Entreprise | 2020 ») ancre
**chaque morceau** dans son propre `<span>`.

### Erreurs

| Statut | `details.reason` | Quand |
|---|---|---|
| 401 | `unauthenticated` / `session_expired` | sans session |
| 403 | `template_not_owned` | modèle Premium non possédé (hors `sample`) |
| 404 | `template_not_found` | modèle inconnu ou non publié |
| 422 | `nothing_to_render` | ni `cv`, ni `letter`, ni `sample` |
| 422 | `render_too_large` | HTML > 1,5 Mo (`details.max_bytes`) |
| 429 | `render_rate_limited` | plus de **90 rendus par minute** par compte (`details.retry_after`) |

### Façade pour un autre module

`documents.build_render_document(settings, storage, cvs=…)` (hors requête) et
`documents.get_render_document` (dépendance FastAPI) rendent le cas d'usage
`RenderDocument.execute(user_id, template_id, cv=…, letter=…, sample=…, ats_safe=…)` →
`RenderedDocument(html, pages, warnings)`. Le module demandeur déclare son port,
`bootstrap/wiring.py` branche (règle 4).

---

## 2. La lettre de motivation

### Le document

```json
{
  "recipient": { "company": "Acme", "logo_url": "https://storage.karria.fr/karria-production-logos/<condensat>/256.webp", "city": "Lyon" },
  "subject": "Candidature au poste de vendeuse",
  "greeting": "Madame, Monsieur,",
  "paragraphs": [
    { "kind": "interet_entreprise", "text": "…" },
    { "kind": "interet_poste", "text": "…" },
    { "kind": "competences", "text": "…" },
    { "kind": "pourquoi_moi", "text": "…" }
  ],
  "closing": "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.",
  "sender": { "first_name": "Awa", "last_name": "Diallo", "headline": "Vendeuse", "email": "…", "phone": "…", "city": "Montreuil" }
}
```

- `kind` ∈ `interet_entreprise` · `interet_poste` · `competences` · `pourquoi_moi` · `libre`.
  Un paragraphe vide est ignoré. ≤ 8 paragraphes, ≤ 2 500 caractères chacun.
- `greeting` et `closing` facultatifs : formules sobres par défaut.
- `logo_url` : **uniquement** une adresse de notre stockage public (celle que donne
  `company.logo` / `company.logo_large` du fil d'offres). Ailleurs : ignorée, rien de rendu,
  avertissement `logo_unavailable`.
- La date et le lieu sont posés par l'API (« Montreuil, le 5 novembre 2026 », ville de
  `sender.city` ; sans ville : « Le 5 novembre 2026 »).
- La lettre est rendue **dans le modèle de CV** : mêmes polices, couleur d'accent des
  titres pour l'objet, marges extérieures du modèle. CV et lettre forment un dossier.

### Rendu ancré

`POST /documents/{template_id}/render` avec `{ "letter": … }`. Chemins :
`sender.full_name`, `sender.headline`, `sender.phone`, `sender.email`, `sender.city`,
`recipient.company`, `recipient.city`, `subject`, `greeting`, `paragraphs.i.text`,
`closing`. `pages` vaut toujours 1 (la page s'ajuste ; au-delà, `window.karriaFit`).

### `POST /documents/letters/export` → `201 GeneratedCv`

```json
{ "template_id": "…" | null, "letter": { … }, "formats": ["pdf", "docx"] | null, "dossier_id": "…" | null, "primary_color": "#7B1E2B" | null }
```

`primary_color` : la couleur du dossier, pour que la lettre suive le CV (§ 1) ; sans
`template_id` ni couleur, celle du choix de la personne (`/user-templates/me/selected`).

Sans `template_id`, le modèle choisi (`PUT /user-templates/me/selected`) ; sans choix,
`404 template_not_found`. Modèle Premium non possédé : `403 template_locked`. Format
inconnu : `422 invalid_format`. Gotenberg absent : `503 rendering_unavailable`.

Réponse : un `GeneratedCv` (même forme que l'export de CV) avec `kind: "letter"`,
`company_name`, `dossier_id`, `pdf_url` / `docx_url` signées 1 h (noms de téléchargement
« Lettre de motivation - Acme.pdf »), `fit`, et `ats: { "explanation": null,
"explanation_status": "unavailable" }` — **pas de score ATS sur une lettre**.

### Les exports, CV et lettres ensemble

- `GeneratedCv` porte désormais `kind` (`cv` | `letter`), `dossier_id` (UUID libre, posé
  par le module des dossiers) et `company_name` (lettre).
- `POST /cv/me/generated-cvs` accepte `dossier_id`, et **`cv`** : un CV complet (même forme et
  mêmes bornes que le `cv` du rendu ancré, § 1) exporté **à la place** du CV de base — c'est
  ainsi qu'un dossier de candidature exporte sa copie adaptée (PDF, DOCX, score ATS sur ce
  CV-là). La photo reste celle du CV de la personne. Absent : comportement inchangé (le CV de
  base, `409 cv_missing` s'il n'existe pas). `cv` hors bornes → `422`.
- `POST /cv/me/generated-cvs` accepte aussi **`primary_color`** (§ 1) ; sans `template_id` ni
  couleur, celle du choix de la personne. `GeneratedCv` porte **`primary_color`** (`#RRGGBB`
  ou `null`) : la couleur avec laquelle l'export a été rendu, pour que « Mes CV » la montre et
  la réutilise (« Voir avec un autre modèle » repart avec elle).
- `GET /cv/me/generated-cvs?kind=cv|letter&dossier_id=…` filtre ; `kind` inconnu → `422`.
- `GET` / `DELETE /cv/me/generated-cvs/{id}` valent pour les deux genres.

### Le pied de page Karria (2026-11-05, ADR-0035)

Chaque PDF et chaque DOCX exporté — CV et lettre, variante ATS safe comprise — porte une ligne
de pied discrète : « CV réalisé avec Karria · karria.fr/app » (« Lettre réalisée avec Karria ·
… »), 7 pt, gris doux, centrée, le lien cliquable vers
`https://karria.fr/app?utm_source=document&utm_medium=cv|letter`. En HTML elle est posée en
absolu au bas de la feuille `.page`, hors du flux et sans ancre `data-chemin` : elle ne déplace
rien et ne change pas `pages` ; l'atelier (rendu ancré) la montre aussi. En DOCX c'est le pied de
section, dans la marge basse. Aucun champ ni paramètre ne la pilote.

---

## 3. Envoi par e-mail

### `POST /documents/exports/{generated_id}/send` → `202 Envoi`

```json
{ "to": "rh@acme.fr" }
```

`to: null` (ou corps absent) = **l'adresse du compte**, à pré-remplir côté app.

### `POST /documents/exports/send` → `202 Envoi`

```json
{ "generated_ids": ["<cv>", "<lettre>"], "to": null }
```

Plusieurs documents **dans un seul e-mail** (1 à 4, doublons ignorés) : le PDF de chaque
export quand il existe, sinon le DOCX, nommés « CV - Prénom Nom.pdf », « Lettre de
motivation - Entreprise.pdf ». Le message est un gabarit fixe, **objet fixe** (« Des
documents envoyés depuis Karria »), signé du nom de la personne (« Awa Diallo vous
transmet ses documents de candidature… ») : **aucun texte libre**. Le nom et les noms de
pièces sont filtrés (jeu strict, 60 caractères) et remplacés par « Une personne » /
« CV.pdf » s'ils ressemblent à un lien ou un domaine.

### Réponse (`Envoi`)

```json
{ "id": "…", "status": "queued", "to": "rh@acme.fr", "generated_ids": ["…"], "created_at": "…", "sent_at": null }
```

L'API répond tout de suite ; la remise est un travail de file. `GET /documents/sends/{id}`
→ `200 Envoi` avec `status` ∈ `queued` · `sent` · `failed` (`sent_at` posé à la remise ;
la raison d'un échec reste dans le journal, jamais dans la réponse).

### Erreurs

| Statut | `details.reason` | Quand |
|---|---|---|
| 404 | `generated_cv_not_found` | export inconnu ou d'une autre personne |
| 404 | `send_not_found` | envoi inconnu ou d'une autre personne |
| 409 | `document_file_missing` | export sans fichier |
| 409 | `account_email_unknown` | `to: null` et adresse du compte introuvable |
| 422 | `recipient_invalid` (`field: to`) | adresse mal formée |
| 422 | `documents_count_invalid` | 0 ou > 4 documents |
| 429 | `send_quota_exceeded` | un plafond est atteint (`details.retry_after`, secondes). La réponse ne dit **pas** lequel : **5 envois par jour civil (UTC)** par compte ; vers une adresse **tierce**, 2 envois par jour par compte vers la même boîte (casse et étiquette `+` ignorées), 10 tous comptes confondus, 10 par heure par origine. |

Les quotas comptent les envois demandés qui n'ont pas échoué. Vers l'adresse du compte
(`to: null`, ou la même boîte), seul le quota du compte s'applique. L'adresse est refusée
(`422 recipient_invalid`) dès qu'elle contient `<>(),;:"[]\` ou un caractère de contrôle.
L'app affiche l'adresse avant d'envoyer (« confirmée visuellement ») ; l'API ne demande pas
de seconde confirmation.

---

## 4. `GET /app/release?platform=ios|android&build=<entier>` → `200`

**Public** (sans session), compté par origine comme la galerie (`429
anonymous_rate_limited`, 60 lectures par 10 min). `build` est le numéro de build natif
installé (entier ≥ 0).

```json
{ "status": "ok", "latest_build": 0, "minimum_build": 0, "store_url": "" }
```

- `status` : `update_required` si `build < minimum_build` (et `minimum_build > 0`) ;
  `update_available` si `build < latest_build` ; sinon `ok`.
- Réglages serveur `APP_RELEASE__{IOS,ANDROID}_{MINIMUM_BUILD,LATEST_BUILD,STORE_URL}` ;
  sans réglage (`0`, `0`, vide), toujours `ok`. `store_url` vide tant que la fiche n'existe
  pas : ne rien rendre cliquable.
- `422` : plateforme inconnue, build négatif ou absent.
