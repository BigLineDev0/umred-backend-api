from .models import Notification


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