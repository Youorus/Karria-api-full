"""Routes du module `payments` : catalogue, ouvrir, relire, gérer, revenir, recevoir Stripe.

Toutes exigent la session de la personne, sauf deux : la **page de retour**,
où Stripe dépose la personne et qui la renvoie vers l'app ; et le **webhook**,
appelé par Stripe et authentifié par sa signature.
"""

from __future__ import annotations

import html
from typing import Annotated, Final

from fastapi import APIRouter, Header, Path, Query, Request, status
from fastapi.responses import HTMLResponse

from karria.modules.payments.api.dependencies import (
    BillingOverviewDep,
    FlagsDep,
    GetCatalogDep,
    HandleWebhookDep,
    OpenCheckoutDep,
    OpenPortalDep,
    PaymentStatusDep,
)
from karria.modules.payments.api.schemas import (
    BillingMeResponse,
    CatalogResponse,
    CheckoutSessionResponse,
    CreateCheckoutSessionRequest,
    PaymentStatusResponse,
    PortalRequest,
    PortalResponse,
    WebhookAcknowledged,
)
from karria.modules.payments.domain import validate_return_url, with_query
from karria.shared.auth import CurrentPrincipalDep
from karria.shared.config import Settings
from karria.shared.errors import ErrorCode, error_responses
from karria.shared.errors.exceptions import InvalidInputError
from karria.shared.http.dependencies import SettingsDep

router = APIRouter(prefix="/payments", tags=["Paiements"])

#: Stripe signe des corps de quelques kilo-octets ; au-delà, ce n'est pas lui.
WEBHOOK_MAX_BYTES = 256 * 1024
#: Chemin de la page de retour, relatif au préfixe du routeur.
RETURN_PATH: Final = "/return"

#: La page où Stripe dépose la personne. Autonome (aucune ressource distante),
#: elle renvoie vers la destination **validée** — l'app ou karria.fr — et
#: laisse un lien si le renvoi automatique ne part pas. Elle ne sait rien et
#: n'annonce rien : seule la notification signée dit « payé », et l'app la
#: lit par `GET /payments/me` ou `GET /payments/status/{id}`.
_PAGE: Final = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Karria — retour à l'application</title>
{refresh}
</head>
<body style="margin:0;min-height:100vh;display:flex;align-items:center;
justify-content:center;background:#f6f8fc;color:#0f172a;
font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
<main style="max-width:22rem;padding:2rem;text-align:center">
<h1 style="font-size:1.25rem;margin:0 0 .75rem">{title}</h1>
<p style="margin:0 0 1rem">{body}</p>
{link}
</main>
{script}
</body>
</html>
"""

_TITLES: Final[dict[str, tuple[str, str]]] = {
    "success": (
        "Merci !",
        "Tu peux revenir dans Karria : ce que tu viens de payer s'y met à jour dès que notre "
        "prestataire nous l'a confirmé — quelques secondes.",
    ),
    "cancelled": ("Rien n'a été payé", "Tu peux revenir dans Karria quand tu veux."),
    "portal": ("C'est noté", "Tes changements se liront dans Karria au retour."),
}


def _return_page(settings: Settings) -> str:
    """L'adresse publique de la page de retour, composée côté serveur (jamais depuis `Host`)."""
    return settings.app.public_api_url(f"{router.prefix}{RETURN_PATH}")


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    summary="Le catalogue",
    description=(
        "Le plan « Karria sans pub », les paliers de cafés et les modèles de CV Premium en "
        "vente, en centimes d'euro TTC. `enabled` dit, par plateforme, si l'app peut proposer "
        "un paiement : faux sans prestataire configuré ou si Marc l'a éteint."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def read_catalog(
    principal: CurrentPrincipalDep, use_case: GetCatalogDep, flags: FlagsDep
) -> CatalogResponse:
    """Rend le catalogue."""
    del principal
    return CatalogResponse.present(await use_case.execute(), flags)


@router.post(
    "/checkout-session",
    response_model=CheckoutSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ouvrir une session Stripe Checkout",
    description=(
        "L'abonnement « sans pub » (4,99 €/mois, sans engagement), des cafés (1, 2, 3 ou 5) "
        "ou un modèle de CV Premium. Rend l'adresse de la page de paiement. Ne vend rien par "
        "lui-même : l'effet suit la notification signée de Stripe (`fulfilled`).\n\n"
        "- `422 return_url_not_allowed`, `coffee_count_out_of_bounds`, `template_id_required` ;\n"
        "- `404 template_not_found` : modèle inconnu, non publié ou gratuit ;\n"
        "- `409 subscription_already_active` (avec `payment_failed`), `template_already_owned` ;\n"
        "- `503 dependency_failure` sans prestataire configuré."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.INVALID_INPUT,
        ErrorCode.NOT_FOUND,
        ErrorCode.ALREADY_EXISTS,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def create_checkout_session(
    body: CreateCheckoutSessionRequest,
    principal: CurrentPrincipalDep,
    open_checkout: OpenCheckoutDep,
    settings: SettingsDep,
) -> CheckoutSessionResponse:
    """Ouvre."""
    opened = await open_checkout.execute(
        principal.subject_id,
        body.to_order(),
        return_url=body.return_url or settings.payments.return_url,
        return_page=_return_page(settings),
    )
    return CheckoutSessionResponse(
        checkout_url=opened.session.url,
        session_id=opened.session.id,
        payment_type=opened.kind.value,
        mode=opened.mode,
        amount_cents=opened.amount_cents,
        currency=opened.currency,
    )


@router.get(
    "/status/{session_id}",
    response_model=PaymentStatusResponse,
    summary="L'état d'un paiement",
    description=(
        "`status` vient de Stripe ; `fulfilled` dit si Karria a livré. "
        "`404 not_found` si la session n'existe pas ou n'a pas été ouverte par la personne."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def get_payment_status(
    session_id: Annotated[str, Path(max_length=128, description="Identifiant Stripe (`cs_…`).")],
    principal: CurrentPrincipalDep,
    get_status: PaymentStatusDep,
) -> PaymentStatusResponse:
    """Relit."""
    record = await get_status.execute(principal.subject_id, session_id)
    return PaymentStatusResponse(
        session_id=record.session_id,
        status="paid" if record.paid else "unpaid",
        payment_type=record.kind,
        fulfilled=record.fulfilled,
    )


@router.get(
    "/me",
    response_model=BillingMeResponse,
    summary="Mes droits : sans pub ou non, et mes cafés",
    description=(
        "Rend toujours une réponse. `ad_free.active` est ce que l'app applique : vrai → aucune "
        "publicité ne se charge. `until` est la fin connue (période payée, ou grâce de 7 jours "
        "après un prélèvement en échec). `enabled` : l'interrupteur par plateforme."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def read_me(
    principal: CurrentPrincipalDep, overview: BillingOverviewDep, flags: FlagsDep
) -> BillingMeResponse:
    """Rend l'état de la personne."""
    return BillingMeResponse.present(await overview.execute(principal.subject_id), flags)


@router.post(
    "/portal-session",
    response_model=PortalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ouvrir le portail Stripe",
    description=(
        "Carte, reçus, résiliation de l'abonnement — hébergé par Stripe. "
        "`404 billing_account_not_found` si aucun paiement n'a jamais été ouvert ; "
        "`503` sans prestataire."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.INVALID_INPUT,
        ErrorCode.NOT_FOUND,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def open_portal_session(
    principal: CurrentPrincipalDep,
    portal: OpenPortalDep,
    settings: SettingsDep,
    body: PortalRequest | None = None,
) -> PortalResponse:
    """Rend l'adresse du portail."""
    url = await portal.execute(
        principal.subject_id,
        return_url=(body.return_url if body is not None and body.return_url else None)
        or settings.payments.return_url,
        return_page=_return_page(settings),
    )
    return PortalResponse(url=url)


@router.get(
    RETURN_PATH,
    include_in_schema=False,
    response_class=HTMLResponse,
    summary="Page d'atterrissage après Stripe",
)
async def payment_return(
    to: Annotated[str, Query(max_length=2_048)] = "",
    status_: Annotated[str, Query(alias="status", max_length=16)] = "success",
    session_id: Annotated[str, Query(max_length=128)] = "",
) -> HTMLResponse:
    """Renvoie la personne vers l'app (ou karria.fr), si la destination vise Karria.

    Publique, et sans risque : elle ne sait rien et n'annonce rien. La
    destination est **revalidée** ici — la règle du domaine, pas la requête —
    sinon la page serait un relais ouvert.
    """
    outcome = status_ if status_ in _TITLES else "success"
    title, body = _TITLES[outcome]
    try:
        destination = validate_return_url(to) if to else None
    except InvalidInputError:
        destination = None
    if destination is not None:
        params = {"status": outcome}
        if session_id:
            params["session_id"] = session_id
        target = html.escape(with_query(destination, **params), quote=True)
        refresh = f'<meta http-equiv="refresh" content="0;url={target}">'
        link = (
            f'<p><a href="{target}" style="display:inline-block;padding:.75rem 1.25rem;'
            "border-radius:999px;background:#1877f2;color:#fff;text-decoration:none;"
            'font-weight:700">Revenir dans Karria</a></p>'
        )
        script = f"<script>location.replace({_js_string(target)});</script>"
    else:
        refresh = link = script = ""
    page = _PAGE.format(
        refresh=refresh,
        title=html.escape(title, quote=False),
        body=html.escape(body, quote=False),
        link=link,
        script=script,
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


def _js_string(escaped_html: str) -> str:
    """Une chaîne JavaScript sûre : les entités HTML sont déjà posées, on cite et borne."""
    return '"' + escaped_html.replace("\\", "\\\\").replace('"', '\\"') + '"'


@router.post(
    "/webhook",
    response_model=WebhookAcknowledged,
    summary="Webhook Stripe",
    description=(
        "Appelé par Stripe. La signature (`stripe-signature`) est vérifiée avec le secret du "
        "webhook ; `422 signature_invalid` sinon. Chaque événement est traité **une fois** "
        "(registre par identifiant) : `checkout.session.completed` livre (café compté, modèle "
        "remis, abonnement relu), `customer.subscription.*` resynchronise, "
        "`invoice.payment_failed` ouvre la grâce. `503` si Stripe n'a pas pu être relu : il "
        "réémettra."
    ),
    responses=error_responses(ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE),
)
async def stripe_webhook(
    request: Request,
    handle: HandleWebhookDep,
    stripe_signature: Annotated[str | None, Header(alias="stripe-signature")] = None,
) -> WebhookAcknowledged:
    """Reçoit."""
    payload = await request.body()
    if len(payload) > WEBHOOK_MAX_BYTES:
        payload = b""  # une signature ne vérifiera jamais un corps tronqué : refus propre
    outcome = await handle.execute(payload, stripe_signature)
    return WebhookAcknowledged(type=outcome.event.type, applied=outcome.applied)
