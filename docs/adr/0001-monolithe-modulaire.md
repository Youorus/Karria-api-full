# ADR-0001 — Monolithe modulaire plutôt que microservices

**Statut :** Acceptée — 2026-10-20, reprise du cadre `zoumani_api`

## Contexte

Karria se reconstruit. Le domaine est connu — la V1 tourne depuis avril 2026 —
mais ses frontières internes n'ont jamais été posées, et l'équipe est réduite
à une personne.

L'expérience de la V1 est instructive dans l'autre sens : c'était un
monolithe, mais un monolithe **non modulaire**. Quarante-cinq dossiers de
premier niveau, aucune frontière, quatre-vingt-dix imports convergeant vers
un même module. Le problème n'était pas d'être un monolithe : c'était de
n'avoir aucune structure interne.

## Décision

Un **monolithe modulaire** : un seul déployable, découpé en modules aux
frontières explicites et vérifiées automatiquement.

## Justification

- **Les frontières d'un domaine inconnu sont fausses au départ.** Découper
  en services aujourd'hui, c'est figer dans du réseau des frontières qu'on
  va vouloir déplacer dans trois mois. Déplacer une frontière interne est un
  refactoring ; déplacer une frontière réseau est une migration.
- **Le coût opérationnel des microservices est immédiat, leur bénéfice est
  différé.** Traçage distribué, cohérence éventuelle, versionnement des
  contrats, orchestration : autant de complexité à payer avant d'avoir le
  moindre utilisateur.
- **Une transaction locale reste possible.** C'est ce qu'un système
  distribué perd en premier, et ce qui coûte le plus cher à reconstruire.

## Conséquences

**Positives**

- Un déploiement, un jeu de logs, un débogueur.
- Refactoring inter-modules trivial tant que tout est en process.
- Extraction d'un module en service possible plus tard : les frontières
  sont déjà posées, seul le transport change.

**Négatives, et comment on les tient**

- *Le monolithe peut redevenir une boule de boue.* → C'est le rôle des
  contrats `import-linter` de l'ADR-0005 : la dérive casse le build.
- *Impossible de mettre à l'échelle un module isolément.* → Accepté. La
  mise à l'échelle horizontale de tout le processus suffit largement à
  l'ordre de grandeur visé, et le jour où ce ne sera plus vrai, ce sera un
  problème enviable.

## Alternatives écartées

- **Microservices dès le départ** — complexité opérationnelle sans
  contrepartie, sur un domaine non stabilisé.
- **Monolithe non structuré** — c'est la V1. On sait ce que ça donne.
