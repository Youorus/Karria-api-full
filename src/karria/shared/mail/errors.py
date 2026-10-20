"""Erreurs du centre d'envoi."""

from __future__ import annotations

from typing import ClassVar

from karria.shared.errors.exceptions import ErrorCode, InfrastructureError


class MailDeliveryError(InfrastructureError):
    """Le message n'a pas pu être remis.

    Traduite en `503` : ce n'est pas la faute de l'appelant, et la bonne
    conduite est de réessayer. Un `500` ferait croire à un défaut de la
    requête, et un client bien élevé n'insisterait pas.

    Le message d'erreur du fournisseur **n'est pas** transmis au client :
    il contient volontiers l'adresse du serveur et le nom du compte
    d'envoi.
    """

    code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_FAILURE

    def __init__(self, detail: str = "") -> None:
        # `details` ne porte que le nom du service : le message du
        # fournisseur contient volontiers l'adresse du serveur et le compte
        # d'envoi, qui n'ont rien à faire dans une réponse HTTP.
        super().__init__(
            "L'envoi du message a échoué. Réessayez dans un instant.",
            details={"service": "mail"},
        )
        #: Conservé pour les journaux, jamais pour la réponse HTTP.
        self.technical_detail = detail


class MailQuotaExhaustedError(MailDeliveryError):
    """Le fournisseur refuse d'envoyer : le quota du jour est épuisé.

    Distincte d'une panne, et la distinction change ce qu'on dit à la
    personne. Une panne se réessaie dans l'instant ; un quota ne se
    libère qu'au lendemain. Inviter à « réessayer » dans ce cas fait
    recliquer dix fois quelqu'un qui devrait simplement recevoir son
    code autrement.

    Le cas est fréquent avec une boîte ordinaire employée comme
    expéditeur : les fournisseurs grand public plafonnent à quelques
    centaines d'envois par jour, seuil qu'une plateforme atteint dès
    ses premiers utilisateurs.
    """

    def __init__(self, detail: str = "") -> None:
        _ = detail
        super().__init__()
        # Le message remplace « réessayez dans un instant », qui est faux
        # ici : le quota ne se libère qu'au lendemain, et l'insistance
        # n'y changera rien.
        self.message = (
            "Nous ne pouvons plus envoyer d'e-mail aujourd'hui. "
            "Réessayez demain, ou écrivez-nous si c'est urgent."
        )
        self.details["reason"] = "mail_quota_exhausted"


class MailAuthenticationError(MailDeliveryError):
    """Le serveur SMTP a refusé les identifiants.

    Distincte d'une panne, et la distinction sert à l'exploitation : une
    panne se résout en attendant, un refus d'authentification ne se
    résout **jamais** tout seul. C'est un mot de passe expiré, révoqué,
    ou une variable d'environnement absente au déploiement.

    Réessayer aggrave le cas : la plupart des fournisseurs bloquent un
    compte après quelques échecs consécutifs, et l'incident passe alors
    d'une minute de configuration à un déblocage manuel.

    Le message rendu au client reste celui d'une indisponibilité : il
    n'a pas à apprendre que notre compte d'envoi est mal configuré.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.details["reason"] = "mail_auth_failed"


class MailRecipientRefusedError(MailDeliveryError):
    """Le serveur a refusé l'adresse du destinataire.

    Là encore, réessayer ne sert à rien : l'adresse est mal formée,
    inexistante, ou son domaine ne reçoit plus. C'est le cas normal d'un
    relais Apple dont la personne a révoqué l'accès — l'adresse était
    valide hier et ne l'est plus.

    Le distinguer permet, plus tard, de marquer l'adresse plutôt que de
    réessayer indéfiniment un envoi qui ne peut pas aboutir.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.details["reason"] = "mail_recipient_refused"


class MailServerUnavailableError(MailDeliveryError):
    """Le serveur n'a pas répondu, ou a coupé la connexion.

    Panne, délai dépassé, connexion refusée. C'est le seul cas où
    « réessayez dans un instant » est un conseil sincère.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.details["reason"] = "mail_server_unavailable"
