# Ce qui est repoussé, et pourquoi

Ce fichier recense **tout ce qui a été identifié puis remis à plus tard**,
avec sa raison. Il n'existe pas pour se donner bonne conscience : une
chose qu'on décide de ne pas faire doit être écrite, sans quoi elle sera
redécouverte dans six mois, sous forme de panne.

Chaque entrée dit ce qui manque, **ce que ça coûte de ne pas le faire**,
et ce qui déclencherait de s'en occuper. Une entrée sans conséquence
identifiée n'a rien à faire ici : elle se supprime.

Tenu à jour à mesure — toute décision de reporter s'y consigne.

---

## Socle

### Le modèle de session n'est pas tranché

`shared/auth/` porte le mécanisme (ADR-0007) ; `resolve_principal` refuse
tout le monde. Le contrat de l'app impose `access_token` + `refresh_token`
et un code à six chiffres par e-mail. Le cadre `zoumani_api` avait tranché
jeton opaque haché en base, rotation du jeton de rafraîchissement,
tolérance d'une minute au rejeu — mais aussi un cookie `httpOnly` et un
frontend en BFF, qui ne s'appliquent pas à une app Expo.

**Ce que ça coûte :** rien tant qu'aucune route protégée n'existe.
**Déclencheur :** le premier module qui porte les comptes. Son ADR dit
ce qu'on reprend de ces décisions et ce qu'on écarte.

### Aucune migration, aucune base de test exercée

`migrations/` est vide et `tests/integration/conftest.py` ignore la suite
tant qu'aucune migration n'existe. Les tests d'intégration du stockage
(MinIO) sont les seuls à tourner.

**Ce que ça coûte :** la chaîne Aerich — `aerich init-db`, `upgrade`,
`downgrade` — n'a pas été exercée sur ce dépôt.
**Déclencheur :** le premier module avec des modèles. Vérifier alors le
`downgrade` autant que l'`upgrade`.

### `docs/DEPLOIEMENT.md` est un squelette

La V1 tournait sur Scaleway et Dokploy, puis a été éteinte (septembre
2026). Le cadre `zoumani_api` déployait par `compose.prod.yaml`. Rien de
cela n'est repris : la cible d'hébergement de la refonte n'est pas décidée.

**Ce que ça coûte :** rien avant le premier déploiement.
**Déclencheur :** la décision d'hébergement. Écrire alors la séquence, et
un `compose.prod.yaml` si c'est le choix.

### Le logo d'e-mail et l'adresse de contact ne sont pas confirmés

`shared/mail/theme.py` pointe `https://karria.fr/icon-512.png` et
`contact@karria.fr`. Ni l'un ni l'autre n'a été vérifié.

**Ce que ça coûte :** un en-tête d'e-mail avec un carré vide, et un pied
qui promet une boîte que personne ne relève.
**Déclencheur :** avant le premier envoi réel (`scripts/test_smtp.py`).
