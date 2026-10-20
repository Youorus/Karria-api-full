"""Catalogue des fournisseurs de stockage objet compatibles S3.

Décrire un fournisseur **en données** plutôt qu'en code est ce qui permet
d'en changer par configuration. Ajouter un fournisseur, c'est ajouter une
entrée ici ; aucun `if provider == ...` ne doit exister ailleurs dans la
base de code — à l'unique exception de la fabrique, qui choisit entre un
adaptateur réseau et un adaptateur en mémoire.

C'est la pièce qui rend la bascule triviale : le protocole S3 est le même
partout, seules changent l'adresse et quelques bornes.

Pourquoi ce catalogue vit dans `config/` et non dans `storage/`
---------------------------------------------------------------
Il décrit **comment se connecter à quoi** : des adresses, des régions, des
bornes. C'est de la configuration, pas de la logique de stockage — et
`StorageSettings` en dépend directement pour dériver son endpoint.

Le placer dans `storage/` créerait un cycle d'import indénouable :
`config` aurait besoin de `storage`, qui a besoin de `shared.errors`, dont
le paquet tire les gestionnaires HTTP, puis la journalisation, puis
`config`. Ici, les dépendances vont dans un seul sens : `storage → config`.

Ce fichier n'importe donc **rien** du projet, volontairement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

#: Plafond de la signature AWS v4, respecté par tous les fournisseurs S3.
SEVEN_DAYS_IN_SECONDS: Final = 604_800


class Provider(StrEnum):
    """Fournisseurs supportés. La valeur est celle de `STORAGE__PROVIDER`."""

    SCALEWAY = "scaleway"
    AWS = "aws"
    CLOUDFLARE_R2 = "cloudflare_r2"
    OVH = "ovh"
    BACKBLAZE = "backblaze"
    #: Développement local et tests d'intégration : même protocole, aucune
    #: facturation, aucun accès réseau sortant.
    MINIO = "minio"
    #: Tests unitaires. Aucune I/O — et refusé en environnement déployé.
    MEMORY = "memory"


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """Tout ce qui distingue un fournisseur S3 d'un autre.

    Attributes:
        endpoint_template: Gabarit de l'URL du service ; `{region}` et
            `{account}` y sont substitués. `None` pour AWS, dont le SDK
            connaît déjà ses propres endpoints.
        default_region: Région appliquée si la configuration n'en donne pas.
        addressing: `virtual` place le bucket dans le nom d'hôte
            (`bucket.s3.fr-par.scw.cloud`), `path` dans le chemin
            (`localhost:9000/bucket`). MinIO n'accepte que le second.
        max_presign_seconds: Plafond de validité d'une URL présignée.
        supports_object_acl: Certains fournisseurs — R2 — ignorent les ACL
            par objet ; la lecture publique y passe par un domaine dédié.
        requires_account_id: R2 encode l'identifiant de compte dans l'URL.
        supports_bucket_cors: Certains services — MinIO — n'implémentent pas
            `PutBucketCors` et autorisent le partage d'origine globalement.
            Le provisionnement saute alors cette étape au lieu d'échouer.
        durable: Les octets survivent-ils au redémarrage du processus ?
            `False` pour le seul adaptateur en mémoire, ce qui suffit à le
            faire refuser sur un environnement déployé — sans qu'aucune
            validation n'ait à nommer un fournisseur.
    """

    endpoint_template: str | None
    default_region: str
    addressing: Literal["virtual", "path"]
    max_presign_seconds: int = SEVEN_DAYS_IN_SECONDS
    supports_object_acl: bool = True
    requires_account_id: bool = False
    supports_bucket_cors: bool = True
    durable: bool = True


#: Les endpoints Scaleway et AWS sont ceux employés en production sur
#: la V1 `karria_api`. Les autres proviennent de la documentation publique de
#: chaque fournisseur : **les reconfirmer le jour où l'un d'eux est
#: activé**. Une URL d'endpoint est exactement le genre de détail qui bouge
#: sans prévenir.
PROFILES: Final[dict[Provider, ProviderProfile]] = {
    Provider.SCALEWAY: ProviderProfile(
        endpoint_template="https://s3.{region}.scw.cloud",
        # Paris. Alternatives : `nl-ams` (Amsterdam), `pl-waw` (Varsovie).
        default_region="fr-par",
        addressing="virtual",
    ),
    Provider.AWS: ProviderProfile(
        endpoint_template=None,
        default_region="eu-west-3",
        addressing="virtual",
    ),
    Provider.CLOUDFLARE_R2: ProviderProfile(
        endpoint_template="https://{account}.r2.cloudflarestorage.com",
        default_region="auto",
        addressing="virtual",
        supports_object_acl=False,
        requires_account_id=True,
    ),
    Provider.OVH: ProviderProfile(
        endpoint_template="https://s3.{region}.io.cloud.ovh.net",
        default_region="gra",
        addressing="virtual",
    ),
    Provider.BACKBLAZE: ProviderProfile(
        endpoint_template="https://s3.{region}.backblazeb2.com",
        default_region="eu-central-003",
        addressing="virtual",
    ),
    Provider.MINIO: ProviderProfile(
        # Adresse du conteneur local. Pour joindre MinIO autrement — depuis
        # un autre conteneur, par exemple — passer par
        # `STORAGE__ENDPOINT_URL`, l'échappatoire prévue à cet effet.
        endpoint_template="http://localhost:9000",
        # `us-east-1` et non l'hôte : `botocore` **valide le format** d'une
        # région et refuse tout ce qui n'y ressemble pas. MinIO l'ignore
        # complètement, mais le SDK, lui, en exige une plausible.
        default_region="us-east-1",
        addressing="path",
        # MinIO répond `NotImplemented` à `PutBucketCors` : il autorise le
        # partage d'origine globalement, sans configuration par bucket.
        supports_bucket_cors=False,
    ),
    Provider.MEMORY: ProviderProfile(
        endpoint_template=None,
        default_region="local",
        addressing="path",
        # Tout est perdu au redémarrage, et sans le moindre message : c'est
        # ce drapeau qui interdit ce fournisseur hors du local.
        durable=False,
    ),
}
