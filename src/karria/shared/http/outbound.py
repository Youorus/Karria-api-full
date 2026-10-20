"""Le client des appels sortants.

═══ Pourquoi il existe ═══

Sept adaptateurs faisaient exactement la même chose, chacun de son côté :
FlightAware, Sendcloud (deux fois), Nominatim, les suggestions d'adresse,
l'envoi de SMS OVH, Stripe. Le même `asyncio.to_thread`, le même
`urllib.request`, le même `# noqa: S310`, la même traduction d'exceptions.

Sept copies d'une trentaine de lignes, c'est sept endroits où corriger un
délai d'attente mal réglé, sept endroits où ajouter une reprise sur
erreur, et sept occasions d'oublier l'un d'eux.

═══ Pourquoi `urllib` et non `httpx` ═══

`httpx` n'est pas une dépendance de production de ce projet — il n'y
figure que pour les tests. L'ajouter pour un client d'une centaine de
lignes serait une dépendance de plus à suivre et à mettre à jour, pour
un gain que la bibliothèque standard couvre déjà.

Le prix est connu : `urllib` est bloquant, donc chaque appel part dans un
fil d'exécution. C'est ce que faisaient déjà les sept adaptateurs.

═══ Ce qu'il fait, et ce qu'il ne fait pas ═══

Il envoie du JSON, il en reçoit, il retente ce qui mérite de l'être. Il
ne connaît ni authentification ni format d'erreur : ce sont les
particularités de chaque fournisseur, et les mettre ici les mélangerait
toutes.

Une seule règle de reprise, et elle est délibérément étroite : on ne
retente **que** ce qui a une chance d'aboutir — une panne réseau, un
429, un 5xx. Retenter un 400 ou un 401 ne fait que répéter la même
erreur en la retardant.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Final

import structlog

logger = structlog.get_logger(__name__)

#: Délai d'attente par défaut. Assez large pour un fournisseur lent,
#: assez court pour ne pas retenir une requête entrante derrière lui.
DEFAULT_TIMEOUT_SECONDS: Final = 10.0

#: Codes qui méritent une seconde tentative. `429` dit « trop vite »,
#: les `5xx` disent « pas maintenant ». Tout le reste dit « jamais ».
RETRYABLE_STATUSES: Final = frozenset({429, 500, 502, 503, 504})

#: Nombre de tentatives, la première comprise. Trois est le compromis
#: usuel : la deuxième rattrape les micro-coupures, la troisième les
#: redémarrages courts, la quatrième ne rattrape plus que de la patience.
DEFAULT_ATTEMPTS: Final = 3

#: Attente avant la seconde tentative, doublée à chaque fois. Un délai
#: fixe fait repartir tous les clients ensemble après une panne, et
#: achève le service qui vient de se relever.
BACKOFF_BASE_SECONDS: Final = 0.5


class OutboundError(Exception):
    """Un appel sortant n'a pas abouti.

    Porte le texte du fournisseur quand il en a donné un : sans lui, le
    diagnostic se réduit à « ça n'a pas marché ».
    """

    def __init__(self, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass(frozen=True, slots=True)
class JsonResponse:
    """Ce qu'un appel rend quand il aboutit."""

    status: int
    body: Any

    @property
    def mapping(self) -> dict[str, Any]:
        """Le corps, s'il est bien un objet JSON.

        Un fournisseur qui renvoie une liste ou une chaîne là où l'on
        attend un objet est une panne, pas un cas à traiter : la lever ici
        évite un `AttributeError` trois couches plus loin.
        """
        if not isinstance(self.body, dict):
            raise OutboundError("réponse inattendue", status=self.status)
        return self.body


@dataclass(frozen=True, slots=True)
class JsonClient:
    """Un client JSON pour un fournisseur donné.

    Attributes:
        base_url: Racine des appels. Le chemin passé à `post` ou `get` s'y
            ajoute, ce qui évite de recomposer l'URL à chaque appel.
        headers: En-têtes constants — jeton d'API, `Accept`. Les secrets
            n'apparaissent jamais dans les journaux : seul l'hôte y figure.
        timeout_seconds: Voir `DEFAULT_TIMEOUT_SECONDS`.
        attempts: Voir `DEFAULT_ATTEMPTS`. Mettre 1 désactive la reprise,
            ce qui est le bon réglage pour un appel non idempotent.
    """

    base_url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    attempts: int = DEFAULT_ATTEMPTS

    async def post_json(self, path: str, payload: Any) -> JsonResponse:
        """Envoie un corps JSON et rend la réponse décodée."""
        return await self._call("POST", path, payload)

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> JsonResponse:
        """Interroge et rend la réponse décodée."""
        suffixe = f"?{urllib.parse.urlencode(params)}" if params else ""
        return await self._call("GET", f"{path}{suffixe}", None)

    async def _call(self, method: str, path: str, payload: Any) -> JsonResponse:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        derniere: OutboundError | None = None

        for tentative in range(1, self.attempts + 1):
            try:
                # `urllib` est bloquant : sans ce fil, l'appel retiendrait
                # la boucle d'événements et toutes les requêtes en cours.
                return await asyncio.to_thread(self._blocking_call, method, url, payload)
            except OutboundError as exc:
                derniere = exc
                if not _worth_retrying(exc) or tentative == self.attempts:
                    break
                attente = BACKOFF_BASE_SECONDS * (2 ** (tentative - 1))
                logger.info(
                    "outbound.retry",
                    host=urllib.parse.urlsplit(url).netloc,
                    status=exc.status,
                    attempt=tentative,
                    wait=attente,
                )
                await asyncio.sleep(attente)

        assert derniere is not None  # noqa: S101 - la boucle s'exécute au moins une fois
        raise derniere

    def _blocking_call(self, method: str, url: str, payload: Any) -> JsonResponse:
        """L'appel réel. Toute panne devient `OutboundError`."""
        corps = None if payload is None else json.dumps(payload).encode("utf-8")
        entetes = {"Accept": "application/json", **self.headers}
        if corps is not None:
            entetes["Content-Type"] = "application/json"

        requete = urllib.request.Request(  # noqa: S310 - schéma fixé par `base_url`
            url, data=corps, headers=entetes, method=method
        )
        try:
            with urllib.request.urlopen(requete, timeout=self.timeout_seconds) as reponse:  # noqa: S310
                return JsonResponse(reponse.status, _decode(reponse.read()))
        except urllib.error.HTTPError as exc:
            # Le corps d'une erreur porte souvent le motif exact. Le jeter
            # laisserait « HTTP 400 » pour seule explication.
            raise OutboundError(_describe(exc), status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OutboundError(f"injoignable : {exc}") from exc


def _worth_retrying(exc: OutboundError) -> bool:
    """Une seconde tentative a-t-elle une chance ?"""
    # `status is None` : la requête n'a pas abouti du tout — coupure,
    # délai dépassé, DNS. C'est le cas qui se rattrape le mieux.
    return exc.status is None or exc.status in RETRYABLE_STATUSES


def _decode(brut: bytes) -> Any:
    try:
        return json.loads(brut.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OutboundError("réponse illisible") from exc


def _describe(exc: urllib.error.HTTPError) -> str:
    """Compose un message lisible à partir d'une réponse d'erreur."""
    try:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
    except OSError:
        detail = ""
    return f"HTTP {exc.code}{f' — {detail}' if detail else ''}"
