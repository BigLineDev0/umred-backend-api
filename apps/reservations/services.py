from .models import Reservation, StatutReservation, HEURE_OUVERTURE, HEURE_FERMETURE


def creneaux_libres_jour(equipement_id, jour):
    """
    Renvoie les plages horaires libres d'un équipement pour une journée
    donnée, en soustrayant les réservations EN_ATTENTE et VALIDEE de
    l'amplitude d'ouverture. Réutilisé pour proposer des alternatives
    lors d'un conflit de planning.
    """
    reservations = Reservation.objects.filter(
        equipements__id=equipement_id, date=jour,
        statut__in=[StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE],
    ).order_by('heure_debut')

    # Algorithme de balayage : les réservations étant triées par heure de
    # début, un curseur avance de l'ouverture vers la fermeture. Chaque
    # « trou » entre le curseur et le début de la réservation suivante est
    # une plage libre. max() gère les réservations qui se chevauchent ou
    # qui sont incluses dans une précédente (le curseur ne recule jamais).
    curseur = HEURE_OUVERTURE
    libres = []
    for r in reservations:
        if r.heure_debut > curseur:
            libres.append((curseur, r.heure_debut))
        curseur = max(curseur, r.heure_fin)
    if curseur < HEURE_FERMETURE:
        libres.append((curseur, HEURE_FERMETURE))
    return libres