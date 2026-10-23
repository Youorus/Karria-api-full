"""Le collecteur d'offres : un processus à part, qui ne connaît de l'API que son contrat.

Contrat
-------
- **Ce qu'il possède** : interroger les sites d'offres (JobSpy : Indeed,
  HelloWork), les mandataires sortants, le rythme et la politesse envers
  les sites, l'analyse propre à chaque site, la mise au format de remise.
- **Ce qu'il ne possède pas** : la base, l'index, les plongements, la
  déduplication, la normalisation, les alertes — tout cela est le module
  `offers` de l'API, de l'autre côté du contrat.
- **Le contrat** (`docs/collecteur.md`, ADR-0016) tient en deux routes
  authentifiées par jeton de service :
  `GET /api/v1/internal/offers/targets` (quoi collecter) et
  `POST /api/v1/internal/offers/batches` (un lot JSONL gzip).
- **Isolement** : même image que l'API, autre commande
  (`python -m karria.collector`), autre application Dokploy. Un
  bannissement d'IP, une panne de site ou un JobSpy cassé n'atteignent
  jamais l'API : ils ne font qu'appauvrir un passage. La frontière est
  vérifiée par import-linter : ce paquet n'importe ni `karria.modules`, ni
  `karria.bootstrap`, ni la base, et rien côté API n'importe ce paquet.
"""
