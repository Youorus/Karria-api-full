# ADR-0041 — Les recherches enregistrées : chaque recherche continue de surveiller, et se coupe dans les paramètres

**Statut :** Acceptée — 2026-11-20 (demande de Marc : « notifier quand une offre correspond à son dernier critère ou à un de ses critères déjà enregistrés », « qu'il puisse désactiver ou réactiver des critères depuis ses paramètres »)
**Complète :** ADR-0017 (moteur d'offres, alertes par matching inversé), ADR-0019 (mémoire de Karria), ADR-0025 (fil sans compte)
**Contexte :** module `offers` (`domain/saved.py`, `application/saved_searches.py`, `offers_saved_searches`, migration 26)

## Le problème

Une personne n'avait qu'**une** recherche : la recherche en cours
(`offers_criteria`, un point par personne dans l'index des critères). En
demander une autre remplaçait la première — plus aucune alerte pour elle,
plus aucune collecte, et aucun endroit où la retrouver. Les alertes ne se
coupaient que pour la recherche en cours, depuis le fil.

## La décision

**Chaque recherche appliquée est retenue** (`offers_saved_searches`, une par
empreinte de critères, dix au plus par personne — au-delà, la moins récemment
utilisée sort, jamais la recherche en cours).

- **La recherche en cours ne change pas** : elle fait le fil, garde son point
  d'index (identifiant de la personne) et son chemin d'alerte (les offres
  entrent dans le fil, puis notification).
- **Chaque recherche enregistrée a son propre point d'index** (son
  identifiant), écrit quand elle est appliquée. Quand une autre devient la
  recherche en cours, elle continue de recevoir le matching inversé : une
  offre fraîche qui lui convient (filtre dur, porte d'intitulé, score haut,
  ni écartée ni déjà signalée) déclenche une notification. Ses offres
  **n'entrent pas dans le fil** ; la notification ouvre la première et nomme
  les suivantes.
- **Le rythme est celui de la personne**, toutes recherches confondues : pas
  deux notifications en moins de deux heures, quatre par jour au plus, une
  seule par passage même si deux recherches y contribuent.
- **La collecte suit les recherches dont l'alerte est active** : les cibles
  demandées par la personne sont l'union de celles de ses recherches actives.
  Couper une recherche arrête aussi ce que Karria collectait pour elle.
- **Les paramètres** : `GET /search/saved` (la liste, `is_current`,
  `alerts_enabled`), `PUT /search/saved/{id}/alerts` (activer, couper),
  `DELETE /search/saved/{id}` (supprimer ; si c'est la recherche en cours, le
  fil redevient général). La bascule du fil (`PUT /search/criteria/alerts`)
  et celle des paramètres disent la même chose.
- **« Oublier la recherche »** (`DELETE /search/criteria`) ne la supprime
  plus : elle reste enregistrée, alerte coupée, réactivable.
- **Karria le dit une fois, en une phrase**, après une recherche enregistrée
  pour la première fois, et seulement pour les deux premières : « Je vous
  préviens dès qu'une offre correspond ; vous pouvez couper cette alerte dans
  vos paramètres, rubrique « Mes recherches ». » Ensuite elle ne le répète
  plus.
- **À la suppression du compte**, recherches et points d'index partent.

## Ce qui est écarté

- **Refondre l'index autour des recherches** (un seul genre de point) : il
  aurait fallu réécrire le chemin d'alerte de la recherche en cours à la
  veille d'une campagne. Le chemin existant est intact ; le nouveau s'ajoute.
- **Faire entrer les offres des autres recherches dans le fil** : le fil est
  celui de la recherche en cours, classé pour elle.
- **Un rythme par recherche** : dix recherches actives auraient permis
  quarante notifications par jour.

## Conséquences

- Les recherches reprises des comptes existants (migration 26) n'ont pas
  encore leur point d'index : elles sont couvertes tant qu'elles sont la
  recherche en cours, et l'obtiennent quand elles sont appliquées de nouveau.
- Une offre peut être signalée deux fois si elle est ré-indexée après que la
  mémoire des deux cents dernières offres signalées a tourné. Accepté.
- Reste à faire dans l'app : l'écran « Mes recherches » des paramètres.
