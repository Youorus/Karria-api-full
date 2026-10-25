# ADR-0021 — Les entreprises et leurs logos : une identité commune aux sources, un logo stocké chez nous

**Statut :** Proposée — 2026-10-25 (demande de Marc : « des logos partout, c'est crucial pour l'UX »)
**Complète :** ADR-0008 (stockage objet), ADR-0009 (images normalisées), ADR-0016 (moteur d'offres), ADR-0017 (collecte)

## Le problème

La nouvelle base n'avait ni les entreprises ni les logos de la V1, et l'API servait l'adresse
du logo **chez la source**, telle quelle. Mesuré le 2026-10-25 sur les offres actives : France
Travail 2,9 % (2 929 / 101 018), Indeed 57,8 %, HelloWork 92,9 % — et ces adresses-là
expirent (jetons LinkedIn), changent (`?v=` de HelloWork) et livrent l'IP de la personne à un
tiers à chaque carte du fil.

## Décision

1. **Une table `offers_companies`**, dans le module `offers` (une entreprise n'a pas de vie
   hors du corpus : pas de module à part, AGENTS.md § 6.5). Identité : une **clé de nom**
   (pliée, sans forme juridique ni ponctuation : « DECATHLON S.A.S. » → `decathlon`) et un
   **domaine** (`decathlon.fr`), unique ensemble ; un domaine vide vaut « inconnu ». Deux
   homonymes aux domaines différents sont deux entreprises ; une annonce sans domaine se
   rattache à l'homonyme le plus ancien. Les domaines des sites d'offres, réseaux et logiciels
   de recrutement (`indeed.com`, `linkedin.com`, `myworkdayjobs.com`…) n'identifient personne
   (`NON_COMPANY_DOMAINS`).
2. **Chaque offre porte `company_id`** et une **copie** du condensat du logo
   (`company_logo_hash`) : le fil se lit sans jointure, et la copie se rafraîchit en une
   requête quand le logo arrive. Seul le rattachement écrit ces deux colonnes, jamais
   l'ingestion (qui réécrit l'offre entière).
3. **Le logo est téléchargé une fois, normalisé, rangé chez nous** — jamais servi depuis un
   tiers. Rognage des bords unis, mise au carré (fond transparent gardé, sinon la couleur du
   fond d'origine), WebP **128 et 256 px**. Refus : plus grand côté < 48 px (source, V1) ou
   < 96 px (icône de site), rapport > 5:1, aplat (`judge_logo`), adresses génériques
   (`static.licdn.com`, `ghost`, `no-logo`…), condensats génériques mesurés
   (`PLACEHOLDER_HASHES`, vide tant que `scripts.companies shared-logos` n'a rien montré).
4. **Bucket `logos` en lecture publique**, servi par `https://storage.karria.fr`, clé
   `<condensat>/<taille>.webp`, `Cache-Control: public, max-age=31536000, immutable`.
   *Pourquoi pas des URL signées :* une URL signée change à chaque réponse, donc le cache
   disque du téléphone (expo-image, indexé par l'URL) ne sert jamais ; signer 20 logos par
   page coûte, pour une information publique. *Ce qui borne le risque :* c'est le **seul**
   bucket public, il n'accepte que du WebP de 512 Kio au plus, la politique n'autorise que
   `s3:GetObject` (pas de listage), et la clé est un condensat (rien à deviner, rien à
   écraser : un contenu = une clé, partagée entre entreprises qui ont le même logo).
5. **Les candidats, dans l'ordre** : le logo de la source (Indeed > France Travail >
   HelloWork > autre), celui de la V1, puis les icônes du site (déclarées par la page
   d'accueil, `apple-touch-icon.png`, puis le service d'icônes de Google en 256 — appelé par
   le serveur, une fois : Google n'apprend qu'un domaine d'entreprise). Le premier qui passe
   le jugement gagne ; sinon l'app montre les initiales.
6. **La file : un travail par passage, pas par entreprise.** `offers.fetch_logos` (toutes les
   5 min, et dès qu'un lot montre une entreprise à chercher) prend 200 entreprises **sous bail**
   (`FOR UPDATE SKIP LOCKED`, celles qui ont des offres actives d'abord), les traite à 16 en
   parallèle, se re-diffère après deux minutes. Les reprises vivent en base : échec passager
   (délai, 5xx, 429) → 1 h, 6 h, 1 j, 3 j, puis abandon ; échec définitif → l'adresse est
   rejetée pour de bon. Cent mille travaux procrastinate auraient refait l'erreur de la V1
   (80 000 travaux ARQ empilés). Le worker passe de 2 à 3 places.
7. **Politesse et sûreté du téléchargement** : 2 requêtes simultanées et 250 ms entre deux
   départs par site, délais 5 s / 10 s, 2 Mio au plus, 4 redirections suivies à la main, et
   **jamais une adresse non publique** (résolution DNS vérifiée à chaque saut : une URL de
   logo vient d'un tiers).
8. **Contrat** : `company.logo` (128) et `company.logo_large` (256), ou `null`. Le nom `logo`
   de la V1 est gardé ; il change de sens (notre adresse, plus celle de la source).

## Limites connues

- Une icône **SVG** n'est pas rastérisée (aucune dépendance pour cela) : Lidl, par exemple,
  n'a que des SVG et retombe sur Google.
- La résolution DNS est vérifiée avant la requête, pas épinglée : un DNS qui change entre les
  deux (rebinding) passerait. Le serveur n'expose rien de sensible en HTTP sur son réseau ; à
  revoir si cela change.
- Un logo prêt n'est pas rafraîchi quand l'entreprise change de logo (`PLUS-TARD.md`).
