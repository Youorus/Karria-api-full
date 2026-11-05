# Contrat API — Dossiers de candidature

Ce que `karria_api_v2` expose (module `applications`, ADR-0033) pour « Adapter
mon CV à cette offre », la lettre de motivation et l'espace Candidatures.
Préfixe `/api/v1/applications`, session exigée (`Authorization: Bearer`),
erreurs au format `{ error: { code, message, details: { reason, … } } }`.

Quatre routes confient l'offre et le CV à un modèle et passent par la porte du
consentement à l'IA (`403 ai_consent_required`, usage `application`) : créer un
dossier, relancer l'adaptation, ébaucher la lettre, réécrire un paragraphe. Elles
sont limitées à **30 appels par 10 minutes et par compte** (`429 ai_rate_limited`,
`details.retry_after`). Les autres n'appellent aucun modèle.

## Le parcours, en quatre gestes

1. Depuis la fiche d'une offre : `POST /applications` → le dossier, avec la
   copie du CV déjà adaptée par Karria (ou `adaptation: en_cours`, à relire).
2. L'atelier : les propositions s'acceptent, se refusent, se défont
   (`POST /{id}/propositions/{pid}`) ; la personne retouche le CV à la main
   (`PUT /{id}/cv`). Le rendu temps réel, les exports et l'envoi se font avec
   le module `documents` en lui passant `cv` et `dossier_id` (ADR-0029/0030/0031).
3. La lettre : `POST /{id}/letter` (ébauche), `PUT /{id}/letter` (édition),
   `POST /{id}/letter/paragraphs/{index}/regenerate` (un paragraphe).
4. « As-tu candidaté ? » : `POST /{id}/applied` / `DELETE /{id}/applied` ; la
   liste `GET /applications?applied=true` est l'espace Candidatures.

## Le dossier (`Dossier`)

```jsonc
{
  "id": "9a4b…",
  "offer": {
    "offer_id": "fc64…",
    "title": "Conseillère de vente prêt-à-porter",
    "company": "Maison Lemaire",
    "logo_url": "https://…/abc123/128.webp", // stable, ou null (initiales)
    "website": "https://lemaire.example",
    "city": "Lyon 3e",
    "contract": "cdi",
    "contract_label": "CDI",
    "source_url": "https://jobs.example/123", // où postuler
    "excerpt": "Nous recherchons une conseillère…", // une ligne
    "description": "Nous recherchons…", // le texte complet, figé
    "keywords": ["conseillere de vente", "relation client", "caisse", "…"],
    "published_at": "2026-11-01T00:00:00Z"
  },
  "template_id": null, // le modèle de CV choisi, ou null
  "cv": {
    /* la copie adaptée, au format GET /cv/me, SANS image_url ni id */
  },
  "cv_base_hash": "3f9a…",
  "base_changed": false, // le CV de base a changé (ou disparu) depuis la copie
  "propositions": [
    {
      "id": "7c1d…",
      "path": "identity.headline",
      "before": "Vendeuse",
      "after": "Conseillère de vente",
      "reason": "L'offre nomme le poste ainsi.",
      "state": "acceptee" // proposee | acceptee | refusee
    },
    {
      "id": "8e2f…",
      "path": "experiences.0.missions.0",
      "before": "Accueillir les clients",
      "after": "Accueillir et conseiller les clients",
      "reason": "L'offre insiste sur le conseil.",
      "state": "proposee"
    },
    {
      "id": "9f30…",
      "path": "skills",
      "before": [{ "id": "…", "name": "Caisse", "level": "advanced" }, "…"],
      "after": [{ "id": "…", "name": "Relation client", "level": "expert" }, "…"],
      "reason": "La relation client d'abord.",
      "state": "proposee"
    }
  ],
  "letter": null, // ou Letter, voir plus bas
  "coverage": { "present": ["caisse", "…"], "missing": ["merchandising"], "ratio": 0.75 },
  "status": "brouillon", // brouillon | pret | candidate
  "adaptation": "faite", // en_cours | faite | echouee
  "adaptation_error": null, // ai_unavailable | ai_output_invalid | queue_unavailable si echouee
  "applied_at": null,
  "created_at": "2026-11-05T10:00:00Z",
  "updated_at": "2026-11-05T10:00:04Z"
}
```

- **Le CV de base n'est jamais touché.** `cv` est une copie ; `base_changed`
  dit si l'original a bougé depuis (l'app propose alors « repartir du CV de
  base » = supprimer le dossier et le recréer).
- **La photo** reste celle du CV de base : `cv` n'a pas d'`image_url`. Pour
  rendre ou exporter, l'app passe ce `cv` et `dossier_id` à `documents`.
- **`path`** désigne un emplacement de `cv` : `identity.headline`, `summary`,
  `experiences.<i>.position`, `experiences.<i>.missions.<j>`, `skills` (l'ordre
  des compétences : `after` est la liste réordonnée), `experiences` (l'ordre).
  Karria n'ajoute jamais un élément ; elle reformule ou réordonne.
- **Par défaut Karria applique** l'intitulé et le « à propos » (`acceptee`,
  `before` conservé) : l'app montre « ton à-propos a été mis à jour » avec
  « revenir ». Le reste arrive `proposee`. Une valeur proposée hors des bornes
  de son chemin (intitulé sur une ligne, 150 caractères ; à-propos 1 500 ;
  mission 300), ou qui rendrait le CV invalide, n'est **pas** appliquée : elle
  reste `proposee`, et l'accepter répond `409 proposition_invalid`.
- **Rien n'est écrasé** : une retouche faite pendant que Karria travaille est
  conservée (les propositions sont rejouées dessus, `before` est la retouche) ;
  pour la lettre, le serveur répond `409 dossier_conflict` si le dossier a bougé
  pendant l'appel — relire, réessayer.
- **`status`** se déduit : `brouillon` tant que l'adaptation court ou qu'une
  proposition est `proposee` ; `pret` ensuite ; `candidate` dès `applied_at`.
- **`coverage`** : les mots-clés de l'offre qui se lisent (mot entier,
  singulier/pluriel, sans accents) dans le CV adapté, recalculés à chaque
  modification. Ce n'est pas le score ATS de `documents`.

## La lettre (`Letter`)

```jsonc
{
  "recipient": "Madame, Monsieur,",
  "subject": "Candidature au poste de Conseillère de vente",
  "paragraphs": [
    { "kind": "interet_entreprise", "text": "…" },
    { "kind": "interet_poste", "text": "…" },
    { "kind": "competences", "text": "…" },
    { "kind": "pourquoi_moi", "text": "…" }
  ],
  "closing": "Je vous prie d'agréer, Madame, Monsieur, mes salutations distinguées."
}
```

`kind` ∈ `interet_entreprise | interet_poste | competences | pourquoi_moi | libre`.
1 à 6 paragraphes, 1 200 caractères chacun au plus ; `recipient` et `subject`
200 au plus, `closing` 300. Pas de signature : l'app (ou `documents`) l'ajoute
avec le nom du CV. Même forme que ce que `documents` met en page (ADR-0030).

## La carte (`DossierCard`, dans la liste)

```jsonc
{
  "id": "9a4b…",
  "offer": { "offer_id": "…", "title": "…", "company": "…", "logo_url": "…", "city": "…", "contract_label": "CDI", "source_url": "…" },
  "template_id": null,
  "status": "candidate",
  "adaptation": "faite",
  "coverage_ratio": 0.75,
  "pending_propositions": 0,
  "has_letter": true,
  "applied_at": "2026-11-06T09:12:00Z",
  "created_at": "…",
  "updated_at": "…"
}
```

## Les routes

### `POST /applications` → `201 Dossier` (ou `200`)

Corps `{ "offer_id": "<uuid>", "template_id": "<uuid>" | null }` (le modèle se
change ensuite par `PUT /{id}/template`). Copie le CV de
base, fige l'offre, lance l'adaptation. Si le modèle répond en moins de dix
secondes, le dossier arrive `adaptation: faite` avec ses propositions ; sinon
`en_cours`, et l'app relit `GET /{id}` (toutes les 2 s, par exemple) jusqu'à
`faite` ou `echouee`.

Un dossier existe déjà pour cette offre : **`200`** avec l'existant, rien n'est
relancé (l'app propose de le reprendre, ou de le supprimer pour repartir du
CV de base).

Erreurs : `409 cv_missing` (pas de CV de base : envoyer créer ou importer le
CV) ; `404 offer_not_found` ; `403 template_locked` (modèle Premium non
possédé, `details.template_id`) ; `403 ai_consent_required`.

### `GET /applications?applied=false` → `200 { items: [DossierCard…] }`

Du plus récemment modifié au plus ancien. `applied=true` : seulement les
dossiers où la personne a candidaté — l'espace Candidatures.

### `GET /applications/{id}` → `200 Dossier`

`404 dossier_not_found` (y compris le dossier d'une autre personne).

### `DELETE /applications/{id}` → `204`

Le CV de base n'est pas concerné. Les exports déjà faits restent chez
`documents`.

### `POST /applications/{id}/adapt` → `200 Dossier`

Relance l'adaptation après un échec, seulement si `adaptation` vaut `echouee`
(`409 adaptation_not_failed` sinon). Les anciennes propositions sont oubliées ;
le CV du dossier tel qu'il est sert de base. Même borne de dix secondes, puis
file. `403 ai_consent_required`, `503 ai_unavailable`.

### `PUT /applications/{id}/cv` → `200 Dossier`

Corps `{ "cv": { … } }`, le CV entier au format `GET /cv/me` (sans `image_url`),
revalidé par la forme d'un CV (`422 cv_invalid` avec `details.fields:
[{field, message}]`). La couverture est recalculée. Les propositions gardent
leur état ; accepter ou revenir s'applique toujours à l'élément visé s'il
existe encore. `409 adaptation_in_progress` tant que Karria travaille.

### `PUT /applications/{id}/template` → `200 Dossier`

Corps `{ "template_id": "<uuid>" | null }`. Le modèle de CV du dossier change
**à tout moment**, même pendant l'adaptation — c'est ce que la feuille « Avec
quel modèle ? » de l'atelier appelle à chaque choix. Même vérification qu'à
l'ouverture : `403 template_locked` (`details.template_id`) pour un modèle
Premium non possédé ; `404 dossier_not_found`. **Rien d'autre ne change** : le
CV adapté, les propositions et leur état, la lettre, la couverture restent
tels quels ; seul `updated_at` bouge. Idempotent : le même modèle redemandé ne
réécrit rien. `null` : le dossier suit de nouveau le modèle choisi de la
personne (`GET /user-templates/me/selected`).

### `POST /applications/{id}/propositions/{pid}` → `200 Dossier`

Corps `{ "decision": "accepter" | "refuser" | "revenir" }`. `accepter` applique
`after` (état `acceptee`) ; `refuser` remet `before` (état `refusee`) ;
`revenir` remet `before` et laisse la proposition `proposee` (« je verrai »).
`404 proposition_not_found` ; `409 proposition_path_invalid` si l'élément visé
a été retiré du CV à la main.

### `POST /applications/{id}/letter` → `200 Dossier`

Karria écrit la première ébauche à partir de l'offre (et de ce que l'index
sait de l'entreprise) et du CV adapté. Remplace la lettre existante.
`409 adaptation_in_progress` ; `403 ai_consent_required` ; `503 ai_unavailable`
ou `ai_output_invalid`.

### `PUT /applications/{id}/letter` → `200 Dossier`

Corps : une `Letter` entière. `422 letter_invalid` avec `details.fields`.

### `POST /applications/{id}/letter/paragraphs/{index}/regenerate` → `200 Dossier`

Corps `{ "instruction": "plus court" | null }` (300 caractères au plus ; c'est
une donnée pour le modèle, jamais un ordre qu'il suivrait hors du paragraphe).
Un seul paragraphe réécrit, sa sorte conservée. `404 letter_missing` ;
`404 paragraph_not_found` ; `403 ai_consent_required` ; `503 ai_unavailable`.

### `POST /applications/{id}/applied` → `200 Dossier`

`status: candidate`, `applied_at` posé (la première date reste). Idempotent.
Le fil d'offres marque l'offre `applied`.

### `DELETE /applications/{id}/applied` → `200 Dossier`

Retour à `brouillon` ou `pret` ; le fil retire `applied`. Idempotent.

## Les raisons d'erreur, en un tableau

| Statut | `details.reason` | Quand |
|---|---|---|
| 401 | `unauthenticated` / `session_expired` | sans session, ou jeton périmé |
| 403 | `ai_consent_required` | créer, relancer, ébaucher, réécrire sans accord à l'IA |
| 403 | `template_locked` | modèle Premium non possédé (ouverture, ou `PUT /{id}/template`) |
| 404 | `dossier_not_found`, `offer_not_found`, `proposition_not_found`, `letter_missing`, `paragraph_not_found` | |
| 409 | `cv_missing` | pas de CV de base |
| 409 | `adaptation_in_progress` | retoucher ou ébaucher pendant l'adaptation |
| 409 | `adaptation_not_failed` | relancer sans échec |
| 409 | `proposition_path_invalid` | l'élément visé n'existe plus |
| 409 | `proposition_invalid` | la valeur ne tient pas dans son chemin (`details.path`, `details.why`), ou rend le CV invalide |
| 409 | `dossier_conflict` | le dossier a été modifié pendant l'appel au modèle : relire et réessayer |
| 422 | `cv_invalid`, `letter_invalid` | forme refusée, `details.fields` |
| 422 | `cv_too_large` | corps de `PUT /{id}/cv` au-delà de 256 Kio (`details.size`, `details.limit`) |
| 429 | `ai_rate_limited` | plus de 30 appels au modèle en 10 minutes pour ce compte ; `details.retry_after` en secondes |
| 503 | `ai_unavailable`, `ai_output_invalid` | le modèle ne répond pas, ou hors schéma |

## Ce que l'app doit prévoir

- L'usage `application` apparaît dans `purposes` du consentement à l'IA :
  ajouter son libellé (« Adapter ton CV et écrire ta lettre ») ; la version
  change, la feuille de consentement se redemande une fois.
- Le CV d'un dossier n'a pas de photo : pour l'aperçu, reprendre l'`image_url`
  de `GET /cv/me`, ou laisser `documents` la lire.
- Rien n'est jamais perdu : chaque proposition garde `before`, et le CV de
  base reste dans `GET /cv/me`.
