import logging

import httpx
from django.conf import settings

from .models import Notification

logger = logging.getLogger(__name__)

def notifier(destinataire, titre, message, type_notification, instance=None):
    """
    Crée une notification en base, immédiatement visible par le destinataire
    (la petite cloche côté Angular). L'envoi réel par email/SMS via Celery
    est un enrichissement possible plus tard, pas nécessaire pour que la
    fonctionnalité existe déjà de bout en bout.
    """
    return Notification.objects.create(
        destinataire=destinataire,
        titre=titre,
        message=message,
        type=type_notification,
        entite=instance,
    )

def notifier_par_email(destinataire, type_notification: str, contexte: dict):
    """
    Appel best-effort vers n8n — une panne du service d'automatisation
    ne doit jamais empêcher l'action métier principale (valider une
    réservation reste possible même si l'email de notification échoue).
    """
    if not settings.N8N_WEBHOOK_URL:
        return
    try:
        httpx.post(settings.N8N_WEBHOOK_URL, json={
            "type": type_notification,
            "email_destinataire": destinataire.email,
            "prenom": destinataire.prenom,
            **contexte,
        }, timeout=5)
    except Exception as e:
        # Best-effort : on n'interrompt pas l'action, mais on garde une trace.
        logger.warning("Webhook n8n injoignable (%s) : %s", type_notification, e)