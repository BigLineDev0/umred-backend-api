"""
Rappels de réservation (24 h et 1 h avant le créneau), envoyés par Django :
notification dans l'application + email au demandeur.

Exécuté toutes les 5 minutes par le planificateur (ou via la tâche
/api/taches/envoyer-rappels/ sur un hébergement sans planificateur). Les
fenêtres de tolérance (23-25 h, 0-2 h) sont larges pour ne rater aucune
réservation même si une exécution est sautée ; le drapeau rappel_*_envoye
garantit qu'un rappel n'est jamais envoyé deux fois.
"""
from datetime import datetime, timedelta

from django.utils import timezone

from apps.notifications.models import TypeNotification
from apps.notifications.services import notifier

from .models import Reservation, StatutReservation

# champ « déjà envoyé » -> (début de fenêtre, fin de fenêtre) avant le créneau
FENETRES = {
    'rappel_24h_envoye': (timedelta(hours=23), timedelta(hours=25)),
    'rappel_1h_envoye': (timedelta(0), timedelta(hours=2)),
}


def _message(reservation):
    ressources = ', '.join(e.nom for e in reservation.equipements.all())
    lieu = reservation.laboratoire.nom + (f' ({ressources})' if ressources else '')
    return (f"Votre réservation au {lieu} a lieu le {reservation.date:%d/%m/%Y} de "
            f"{reservation.heure_debut:%H:%M} à {reservation.heure_fin:%H:%M}. "
            "Si vous n'en avez plus besoin, annulez-la pour libérer le créneau.")


def a_rappeler(champ, maintenant):
    """Réservations validées dont le début tombe dans la fenêtre du rappel `champ`."""
    debut_fenetre, fin_fenetre = (maintenant + d for d in FENETRES[champ])
    # Date et heure sont deux colonnes : le SQL pré-filtre par date, Python
    # compare ensuite le datetime exact.
    candidates = Reservation.objects.filter(
        statut=StatutReservation.VALIDEE, date__gte=debut_fenetre.date(), date__lte=fin_fenetre.date(),
        **{champ: False},
    ).select_related('demandeur', 'laboratoire').prefetch_related('equipements')
    return [
        r for r in candidates
        if debut_fenetre <= timezone.make_aware(datetime.combine(r.date, r.heure_debut)) <= fin_fenetre
    ]


def envoyer_rappels(maintenant=None):
    """Envoie les rappels dus ; renvoie le nombre de rappels envoyés."""
    maintenant = maintenant or timezone.localtime()
    envoyes = 0
    for champ in FENETRES:
        for reservation in a_rappeler(champ, maintenant):
            # Le drapeau est posé AVANT l'envoi, par une mise à jour
            # conditionnelle : si deux exécutions se chevauchent, une seule
            # obtient la ligne et envoie le rappel.
            if not Reservation.objects.filter(pk=reservation.pk, **{champ: False}).update(**{champ: True}):
                continue
            notifier(reservation.demandeur, 'Rappel de réservation', _message(reservation),
                     TypeNotification.RAPPEL, reservation, email=True)
            envoyes += 1
    return envoyes
