# ADR-0011 — Vérification locale des jetons Apple et Google, rattachement par le `sub`

**Statut :** Acceptée — 2026-10-21
**Complète :** ADR-0010

## Contexte

L'app obtient un `identityToken` d'Apple ou un `idToken` de Google et le
transmet tel quel (`social.ts`) : elle « ne fait que transporter ». C'est
le serveur qui décide qui est cette personne. La V1 vérifiait Google par le
SDK `google-auth`, Apple par `python-jose` avec un cache JWKS maison, et
rattachait par l'adresse e-mail d'abord.

Trois questions : comment prouver un jeton, à quoi rattacher une personne,
et que faire d'une adresse qu'Apple ne renvoie qu'une fois.

## Décision

**1. Les jetons sont des JWT RS256 vérifiés localement**, avec PyJWT et les
clés publiques du fournisseur (JWKS). Cinq contrôles, aucun facultatif :
signature, émetteur, audience, expiration, nonce. Un seul algorithme
accepté (`RS256`). Une seule erreur rendue (`auth_social_token_invalid`),
le détail dans les journaux.

**2. Le nonce est comparé différemment selon le fournisseur.** Google signe
la valeur brute. Apple signe le **SHA-256** que l'app lui a soumis ; le
serveur hache donc le clair reçu avant de comparer.

**3. Les clés publiques viennent d'un port (`JwksSource`).** L'adaptateur
HTTP les met en cache une heure et force une relecture sur `kid` inconnu.
En test, une clé RSA générée localement produit un JWKS, et la vérification
**réelle** s'exerce : signature d'une autre clé, mauvaise audience, jeton
expiré, nonce faux, algorithme `HS256` forgé.

**4. Les audiences sont de la configuration** (`SOCIAL__GOOGLE_*_CLIENT_ID`,
`SOCIAL__APPLE_CLIENT_ID = com.karria.app`). Google émet un client par
plateforme ; le jeton porte celui qui l'a émis, d'où une liste. Sans
audience Google, la route répond `503 auth_social_provider_unavailable`
plutôt que de rejeter chaque jeton — le second message enverrait chercher
du côté de l'app.

**5. Le `sub` du fournisseur prime sur l'adresse.** Une personne qui
revient est reconnue par `(provider, subject)`, unique en base. L'adresse
ne sert qu'à la **première** venue : retrouver un compte existant — par
code e-mail ou par l'autre fournisseur — ou en créer un.

**6. Le rattachement par adresse exige qu'elle soit attestée.** Google dit
`email_verified` et il arrive à faux ; Apple ne signe que des adresses
vérifiées (relais compris). Une adresse non attestée ne rattache ni ne crée
rien (`auth_social_email_unverified`, 409) : un compte Karria est une
adresse **prouvée**. Sans adresse du tout — une première venue Apple dont
l'app aurait perdu l'adresse — on refuse aussi (`auth_social_email_missing`).

## Justification

- **Vérifier localement** : aucun appel réseau par connexion, hors
  rafraîchissement des clés ; aucune dépendance à un SDK par fournisseur.
  `pyjwt[crypto]` est la seule dépendance ajoutée.
- **Le `sub` d'abord** : une adresse change de mains, un relais Apple se
  désactive. Chercher par adresse d'abord, c'est accepter que quiconque
  contrôle une boîte prenne le compte associé.
- **Le port JWKS** : c'est ce qui permet de tester la cryptographie sans
  réseau, donc de la tester tout court. Zoumani s'appuyait sur
  `PyJWKClient` et ne testait que la transformation du nonce ; un défaut
  de comparaison y a refusé toutes les connexions Apple un temps.

## Conséquences

- `identity` possède la table `identity_provider_identities`.
- Les identifiants Google de l'app doivent être recopiés dans `.env`
  (`SOCIAL__GOOGLE_IOS_CLIENT_ID`, `SOCIAL__GOOGLE_WEB_CLIENT_ID`). Au
  moment de cette ADR, le `.env` de l'app ne les porte **pas** : ils sont
  vides, et la connexion Google répond « indisponible » jusqu'à ce qu'ils
  soient posés.
- Le cache JWKS est par processus. Une rotation de clés chez Apple est
  suivie à la première connexion qui présente le nouveau `kid`.

## Alternatives écartées

- **SDK `google-auth` et `python-jose`** (V1) — deux bibliothèques, deux
  chemins de vérification, un cache maison. Les jetons sont des JWT
  standard.
- **Créer un compte sans adresse attestée** (Zoumani) — contredirait
  « un compte est une adresse prouvée », et laisserait un compte que
  personne ne pourrait retrouver par code e-mail.
- **Vérifier le jeton côté app** — n'importe qui pourrait déclarer être
  n'importe qui.
