import logging

import httpx
from django.conf import settings
from django.core.mail import send_mail

from .models import Notification

logger = logging.getLogger(__name__)

def notifier(destinataire, titre, message, type_notification, instance=None, email=False):
    """
    Crée une notification en base, immédiatement visible par le destinataire
    (la petite cloche côté Angular). email=True l'envoie aussi par email :
    réservé aux événements qui demandent une action rapide (créneau libéré,
    demande écartée, réservation compromise), pour ne pas noyer les boîtes.
    """
    notification = Notification.objects.create(
        destinataire=destinataire,
        titre=titre,
        message=message,
        type=type_notification,
        entite=instance,
    )
    if email:
        envoyer_email(destinataire, titre, message)
    return notification


def envoyer_email(destinataire, sujet, message):
    """
    Best-effort : une panne du serveur mail ne doit jamais faire échouer
    l'action métier (valider, annuler...). L'échec est journalisé.
    """
    if not destinataire.email:
        return
    corps = (
        f"Bonjour {destinataire.prenom},\n\n{message}\n\n"
        f"Retrouvez le détail sur la plateforme : {settings.FRONTEND_URL}\n\n"
        f"Cet email est envoyé automatiquement, merci de ne pas y répondre."
    )
    try:
        send_mail(sujet, corps, settings.DEFAULT_FROM_EMAIL, [destinataire.email], fail_silently=False)
    except Exception:
        logger.exception("Échec d'envoi de l'email « %s » à %s", sujet, destinataire.email)

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