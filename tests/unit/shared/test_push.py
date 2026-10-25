"""Le contrat push v1 : chaque type ouvre son écran, et un message hors contrat ne se construit pas.

Ce que ces tests protègent : l'app route sur `data.type` et `data.id`, et les
anciennes versions sur `data.url`. Un écart ici, c'est une notification
touchée qui n'ouvre rien — ce que faisaient les alertes avant ce contrat.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from karria.shared.push import (
    MAX_BODY_LENGTH,
    MAX_TITLE_LENGTH,
    PUSH_CONTRACT_VERSION,
    PushChannel,
    PushKind,
    PushMessage,
)

pytestmark = pytest.mark.unit

ID = UUID("0b6c0f3e-58a4-4c7e-9a43-1f6f1c3b7d2a")


def _message(
    kind: PushKind,
    *,
    target_id: UUID | None = None,
    count: int | None = None,
    campaign: str | None = None,
) -> PushMessage:
    return PushMessage(
        kind=kind, title="Titre", body="Corps", target_id=target_id, count=count, campaign=campaign
    )


@pytest.mark.parametrize(
    ("kind", "target_id", "url", "channel"),
    [
        (PushKind.OFFER, ID, f"karria://offre/{ID}", PushChannel.OFFRES),
        (PushKind.OFFERS_FEED, None, "karria://offres", PushChannel.OFFRES),
        (PushKind.SEARCH_RESULTS, None, "karria://offres", PushChannel.OFFRES),
        (PushKind.ASSISTANT, None, "karria://offres?karria=1", PushChannel.OFFRES),
        (PushKind.CV_READY, None, "karria://cv", PushChannel.DOCUMENTS),
        (PushKind.CV_EXPORT, ID, "karria://cv", PushChannel.DOCUMENTS),
        (PushKind.CV_EXPORT, None, "karria://cv", PushChannel.DOCUMENTS),
        (PushKind.TEMPLATE, ID, f"karria://modele/{ID}", PushChannel.DOCUMENTS),
        (PushKind.TEMPLATES, None, "karria://modeles", PushChannel.DOCUMENTS),
        (PushKind.ACCOUNT, None, "karria://parametres", PushChannel.COMPTE),
    ],
)
def test_chaque_type_porte_sa_version_son_lien_et_son_canal(
    kind: PushKind, target_id: UUID | None, url: str, channel: PushChannel
) -> None:
    message = PushMessage(kind=kind, title="Titre", body="Corps", target_id=target_id)

    expected = {"v": PUSH_CONTRACT_VERSION, "type": kind.value, "url": url}
    if target_id is not None:
        expected["id"] = str(target_id)
    assert message.data == expected
    assert message.channel is channel
    assert message.category == channel.value
    assert message.badge == (1 if channel is PushChannel.OFFRES else None)
    assert all(isinstance(value, str) for value in message.data.values())


def test_le_nombre_et_la_campagne_voyagent_en_chaines() -> None:
    message = PushMessage(
        kind=PushKind.OFFERS_FEED, title="t", body="b", count=12, campaign="v1-retour-2026"
    )
    assert message.data["count"] == "12"
    assert message.data["campaign"] == "v1-retour-2026"


@pytest.mark.parametrize("kind", [PushKind.OFFER, PushKind.TEMPLATE])
def test_un_type_qui_designe_une_chose_exige_son_identifiant(kind: PushKind) -> None:
    with pytest.raises(ValueError, match="identifiant manque"):
        _message(kind)


@pytest.mark.parametrize(
    "kind",
    [
        PushKind.OFFERS_FEED,
        PushKind.SEARCH_RESULTS,
        PushKind.ASSISTANT,
        PushKind.CV_READY,
        PushKind.TEMPLATES,
        PushKind.ACCOUNT,
    ],
)
def test_un_type_general_refuse_un_identifiant(kind: PushKind) -> None:
    with pytest.raises(ValueError, match="ne porte pas d'identifiant"):
        _message(kind, target_id=ID)


@pytest.mark.parametrize(
    ("kind", "count"),
    [(PushKind.CV_READY, 2), (PushKind.ACCOUNT, 1), (PushKind.OFFERS_FEED, 0)],
)
def test_un_nombre_hors_des_offres_ou_nul_est_refuse(kind: PushKind, count: int) -> None:
    with pytest.raises(ValueError, match="nombre"):
        _message(kind, count=count)


@pytest.mark.parametrize("campaign", ["", "Avec Espace", "x" * 65, "-debut"])
def test_une_campagne_est_une_etiquette_courte(campaign: str) -> None:
    with pytest.raises(ValueError, match="campagne"):
        _message(PushKind.OFFERS_FEED, campaign=campaign)


@pytest.mark.parametrize(
    ("title", "body", "field"),
    [
        ("  ", "b", "titre"),
        ("t" * (MAX_TITLE_LENGTH + 1), "b", "titre"),
        ("t", "", "corps"),
        ("t", "b" * (MAX_BODY_LENGTH + 1), "corps"),
    ],
)
def test_titre_et_corps_sont_obligatoires_et_bornes(title: str, body: str, field: str) -> None:
    with pytest.raises(ValueError, match=field):
        PushMessage(kind=PushKind.OFFERS_FEED, title=title, body=body)
