# ADR-0032 — Gabarits d'e-mail : une mise en page sobre, sans étiquette, lisible en clair et en sombre partout

**Statut :** Proposée — 2026-11-05 (écrite par Fable, à valider par Marc)
**Remplace en partie :** la mise en page de `shared/mail/layout.py` du 2026-10-21

## Le problème

Marc : « le modèle actuel est déjà très bien, mais je ne veux plus ces
badges/étiquettes qui font penser à une IA (ces petits onglets avec une barre
et du texte) ; un contenu ultra orienté tech, startup ; parfaitement
responsive sur mobile ; le logo Karria toujours visible ; lisible en mode
sombre comme en clair dans toutes les applications de lecture. »

Les encarts à barre latérale (`warning`), le code dans une boîte bordée et les
mentions grises sous un filet sont la grammaire des produits d'IA de 2025. Le
logo pointait `https://karria.fr/icon-512.png`, qui n'existe pas. Le mode
sombre n'était que déclaré (`color-scheme`), sans couleurs sombres : Apple
Mail inversait de lui-même, Gmail iOS aussi, avec des résultats différents.

## La décision

1. **Aucune pastille, étiquette, encart ni icône.** Un avertissement est une
   phrase ; un code est un grand nombre espacé sur la page, sans boîte. Les
   titres sont courts et en gras ; le reste est du texte courant, à la
   manière des messages d'OpenAI, Stripe ou Linear.
2. **Tableaux imbriqués, CSS en ligne, largeur fluide ≤ 600 px**, pile de
   polices système. Rien d'autre ne marche partout (Outlook rend avec Word).
3. **Mode sombre réel** : `<meta name="color-scheme" content="light dark">`
   **et** un bloc `@media (prefers-color-scheme: dark)` qui recolore fond,
   panneau, texte et liens par classes (`!important`), doublé des sélecteurs
   `[data-ogsc]`/`[data-ogsb]` d'Outlook. Les couleurs claires sont choisies
   pour **rester lisibles inversées** (Gmail iOS ignore la media query et
   inverse : texte `#0F172A` sur `#FFFFFF`, bleu `#1877F2` qui garde son
   contraste dans les deux sens).
4. **Le logo est un PNG hébergé sur notre stockage public**
   (`https://storage.karria.fr/karria-production-assets/brand/karria-mot-logo.png`,
   mot-logo noir à fond transparent, 2×) **et** le mot « Karria » en texte
   juste après : si l'image est bloquée, le nom reste. En sombre, le mot-logo
   noir sur fond transparent deviendrait invisible : l'image sombre et
   l'image claire sont toutes deux dans le HTML, l'une masquée selon le
   thème (classe + `display`), et le mot en texte reste la garantie.
5. **Un gabarit par message, en texte** (inchangé) ; le pied dit pourquoi le
   message arrive, l'adresse où répondre, les pages contractuelles, le
   destinataire. Le gabarit `documents_sent` (ADR-0031) est signé du nom de
   la personne : le registre porte `signature_from`, la clé de contexte qui
   remplace « L'équipe Karria ».

## Conséquences

- Les tests de rendu existants restent (variables, HTML bien formé, version
  texte) ; s'y ajoutent l'absence de bordure latérale et de boîte, la
  présence du bloc sombre et des deux images de logo.
- Le logo doit être déposé dans le bucket `assets` (`scripts/assets.py`) ;
  tant qu'il n'y est pas, l'en-tête n'affiche que le mot.
- Vérification visuelle : `scripts/mail_preview.py` écrit les six gabarits en
  HTML local, en clair et en sombre émulé, pour les ouvrir dans un navigateur.
