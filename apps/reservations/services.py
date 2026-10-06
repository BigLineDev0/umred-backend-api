"""
Moteur de planification : calcul des plages libres, recommandation de
créneaux alternatifs, équipements équivalents, liste d'attente des
créneaux et clôture automatique des réservations passées.

Tout est déterministe et explicable : chaque proposition est accompagnée
d'un message qui dit POURQUOI elle est faite (« disponible à partir de
11h00 », « même horaire le lendemain »...).
"""
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.core.services import enregistrer as journaliser
from apps.notifications.models import TypeNotification
from apps.notifications.services import notifier

from apps.maintenances.models import Maintenance

from .models import (
    AlerteCreneau, Reservation, StatutReservation, STATUTS_BLOQUANTS, STATUTS_EQUIPEMENT_NON_RESERVABLES,
)

JOURS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août',
        'septembre', 'octobre', 'novembre', 'décembre']


def _minutes(t):
    return t.hour * 60 + t.minute


def _heure(minutes):
    return time(minutes // 60, minutes % 60)


def _hhmm(minutes):
    return f'{minutes // 60}h{minutes % 60:02d}'


def _jour_lisible(jour):
    return f'{JOURS[jour.weekday()]} {jour.day} {MOIS[jour.month - 1]}'


def plages_libres(equipements_ids, jour, regles, apres=None):
    """
    Plages (en minutes depuis minuit) où TOUS les équipements demandés sont
    libres en même temps : on fusionne les réservations acquises de chacun,
    puis on prend les trous entre elles dans l'amplitude d'ouverture.

    Algorithme de balayage : les occupations étant triées par début, un
    curseur avance de l'ouverture vers la fermeture ; chaque « trou » entre
    le curseur et l'occupation suivante est une plage libre. max() gère les
    occupations qui se chevauchent (le curseur ne recule jamais).
    """
    occupations = Reservation.objects.filter(
        equipements__id__in=equipements_ids, date=jour, statut__in=STATUTS_BLOQUANTS,
    ).values_list('heure_debut', 'heure_fin').distinct().order_by('heure_debut')

    curseur = _minutes(regles.heure_ouverture)
    if apres is not None:
        curseur = max(curseur, apres)
    fermeture = _minutes(regles.heure_fermeture)
    libres = []
    for debut, fin in occupations:
        debut, fin = _minutes(debut), _minutes(fin)
        if debut > curseur:
            libres.append((curseur, min(debut, fermeture)))
        curseur = max(curseur, fin)
    if curseur < fermeture:
        libres.append((curseur, fermeture))
    return [(a, b) for a, b in libres if b > a]


def proposer_creneaux(equipements_ids, date, heure_debut, heure_fin, regles, nb_jours=7, limite=3):
    """
    Recommande les créneaux les plus PROCHES de la demande : même durée,
    même équipement(s). Score = écart en minutes avec l'heure demandée,
    plus une journée de pénalité par jour de décalage : on préfère donc
    « 11h le même jour » à « 10h le lendemain ». C'est ce qui permet de
    répondre « le créneau sera disponible à partir de 11h00 » quand
    10h-11h est pris et qu'on demande 10h-12h.
    """
    if not equipements_ids:
        return []
    duree = _minutes(heure_fin) - _minutes(heure_debut)
    demande = _minutes(heure_debut)
    maintenant = timezone.localtime()
    aujourd_hui = maintenant.date()
    # Arrondi aux 5 minutes supérieures : on ne propose pas « 10h03 ».
    maintenant_minutes = -(-(maintenant.hour * 60 + maintenant.minute) // 5) * 5
    limite_date = aujourd_hui + timedelta(days=regles.delai_max_jours)

    candidats = []
    for decalage in range(nb_jours):
        jour = date + timedelta(days=decalage)
        if jour < aujourd_hui or jour > limite_date:
            continue
        apres = maintenant_minutes if jour == aujourd_hui else None
        if Maintenance.jour_bloque(equipements_ids, jour).exists():
            continue
        for debut_plage, fin_plage in plages_libres(equipements_ids, jour, regles, apres):
            if fin_plage - debut_plage < duree:
                continue
            # Le début le plus proche de l'heure demandée dans cette plage.
            debut = min(max(demande, debut_plage), fin_plage - duree)
            score = decalage * 24 * 60 + abs(debut - demande)
            candidats.append((score, jour, debut))

    candidats.sort(key=lambda c: c[0])
    propositions = []
    for _, jour, debut in candidats[:limite]:
        fin = debut + duree
        if jour == date and debut > demande:
            type_, message = 'plus_tard', f"Le créneau sera disponible à partir de {_hhmm(debut)}"
        elif jour == date:
            type_, message = 'plus_tot', f"Disponible plus tôt : {_hhmm(debut)} - {_hhmm(fin)}"
        elif debut == demande:
            type_, message = 'autre_jour', f"Même horaire le {_jour_lisible(jour)}"
        else:
            type_, message = 'autre_jour', f"Le {_jour_lisible(jour)} de {_hhmm(debut)} à {_hhmm(fin)}"
        propositions.append({
            'date': jour.isoformat(),
            'heure_debut': _heure(debut).strftime('%H:%M'),
            'heure_fin': _heure(fin).strftime('%H:%M'),
            'type': type_,
            'message': message,
        })
    return propositions


def conflits_detailles(equipements, date, heure_debut, heure_fin):
    """Quels équipements demandés sont déjà pris, et sur quelle plage."""
    ids = {e.id for e in equipements}
    bloquantes = Reservation.chevauchements(
        date, heure_debut, heure_fin, list(ids), STATUTS_BLOQUANTS
    ).prefetch_related('equipements')
    conflits = []
    for r in bloquantes:
        for e in r.equipements.all():
            if e.id in ids:
                conflits.append({
                    'equipement_id': e.id, 'equipement': e.nom,
                    'heure_debut': r.heure_debut.strftime('%H:%M'),
                    'heure_fin': r.heure_fin.strftime('%H:%M'),
                })
    return conflits


def equipements_equivalents(laboratoire, equipements, conflits, date, heure_debut, heure_fin):
    """
    Pour chaque équipement EN CONFLIT, un équipement de la même catégorie,
    dans le même labo, réservable (ni en panne ni en maintenance) et libre
    sur le créneau demandé. 'remplace' indique lequel il remplace, pour que
    le frontend ne touche pas au reste de la sélection.
    """
    from apps.equipements.models import Equipement

    selection = {e.id for e in equipements}
    en_conflit = {c['equipement_id'] for c in conflits}
    resultats, deja_proposes = [], set()
    for equipement in equipements:
        if equipement.id not in en_conflit or not equipement.categorie:
            continue
        candidats = Equipement.objects.filter(
            laboratoire=laboratoire, categorie__iexact=equipement.categorie,
        ).exclude(id__in=selection | deja_proposes).exclude(statut__in=STATUTS_EQUIPEMENT_NON_RESERVABLES)
        for equiv in candidats:
            if not Reservation._a_un_conflit(date, heure_debut, heure_fin, [equiv.id]):
                resultats.append({
                    'id': equiv.id, 'nom': equiv.nom,
                    'remplace': equipement.id, 'remplace_nom': equipement.nom,
                })
                deja_proposes.add(equiv.id)
    return resultats


def analyser_file(reservation, equipements_ids):
    """
    Situation d'une demande dans la file d'attente de son créneau : combien
    d'autres demandes le visent et à quelle place celle-ci serait servie.
    Les demandes concurrentes restent anonymes pour le demandeur.
    """
    concurrentes = list(reservation.concurrentes(equipements_ids).select_related('projet', 'demandeur'))
    if not concurrentes:
        return {'nombre': 0, 'rang': 1, 'message': ''}
    rang = reservation.rang_dans_la_file(concurrentes)
    autres = len(concurrentes)
    message = (
        f"{autres} autre{'s' if autres > 1 else ''} demande{'s' if autres > 1 else ''} en attente sur ce créneau. "
        f"Selon les priorités (projet puis statut académique), la vôtre serait {'en tête' if rang == 1 else f'en position {rang}'}. "
        "Le validateur arbitrera : les demandes non retenues seront prévenues avec des alternatives."
    )
    return {'nombre': autres, 'rang': rang, 'message': message}


# --- Liste d'attente des créneaux ---

def creer_alerte(utilisateur, laboratoire, equipements_ids, date, heure_debut, heure_fin):
    """Idempotent : une seule alerte active par utilisateur et créneau."""
    existante = AlerteCreneau.objects.filter(
        utilisateur=utilisateur, laboratoire=laboratoire, date=date,
        heure_debut=heure_debut, heure_fin=heure_fin, active=True,
    ).first()
    if existante:
        return existante
    alerte = AlerteCreneau.objects.create(
        utilisateur=utilisateur, laboratoire=laboratoire, date=date,
        heure_debut=heure_debut, heure_fin=heure_fin,
    )
    if equipements_ids:
        alerte.equipements.set(equipements_ids)
    return alerte


def liberer_creneau(reservation):
    """
    Une réservation acquise vient d'être annulée : les personnes qui
    attendaient ce créneau sont prévenues (premier arrivé, premier
    informé : l'ordre des alertes suit leur date de création). Renvoie le
    nombre de personnes prévenues.
    """
    equipements = ', '.join(e.nom for e in reservation.equipements.all()) or reservation.laboratoire.nom
    alertes = list(AlerteCreneau.correspondant_a(reservation))
    for alerte in alertes:
        notifier(
            alerte.utilisateur, 'Un créneau que vous attendiez s\'est libéré',
            f"{equipements} est de nouveau libre le {reservation.date:%d/%m/%Y} de "
            f"{reservation.heure_debut:%H:%M} à {reservation.heure_fin:%H:%M}. Réservez-le dès maintenant.",
            TypeNotification.RESERVATION, reservation, email=True,
        )
    AlerteCreneau.objects.filter(pk__in=[a.pk for a in alertes]).update(active=False, date_notification=timezone.now())
    return len(alertes)


def annuler_reservations_futures(utilisateur, par=None):
    """
    Compte désactivé : ses réservations à venir sont annulées, sinon elles
    bloqueraient des créneaux que personne n'utilisera. Les personnes en
    liste d'attente de ces créneaux sont prévenues. Renvoie le nombre annulé.
    'par' est l'administrateur à l'origine de la désactivation (traçabilité).
    """
    maintenant = timezone.localtime()
    a_venir = Reservation.objects.filter(
        Q(date__gt=maintenant.date()) | Q(date=maintenant.date(), heure_debut__gt=maintenant.time()),
        demandeur=utilisateur, statut__in=[StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE],
    ).select_related('laboratoire').prefetch_related('equipements')
    n = 0
    for reservation in a_venir:
        if reservation.annuler(par=par):
            liberer_creneau(reservation)
        journaliser(par, 'Annulation de réservation', reservation, 'Compte du demandeur désactivé')
        n += 1
    return n


# --- Destinataires ---

def validateurs_pour(reservation):
    """
    Qui prévenir d'une nouvelle demande en attente : l'encadrant de
    l'étudiant s'il peut la traiter, sinon les techniciens et admins de
    l'établissement. Évite d'inonder tous les validateurs de la plateforme.
    """
    from apps.utilisateurs.models import Role, StatutCompte, Utilisateur

    encadrant = reservation.demandeur.encadrant
    if encadrant and encadrant.statut_compte == StatutCompte.ACTIF and reservation.peut_statuer(encadrant):
        return [encadrant]
    return list(Utilisateur.objects.filter(
        role__in=[Role.TECHNICIEN, Role.ADMIN], statut_compte=StatutCompte.ACTIF,
        organisation_id=reservation.laboratoire.organisation_id,
    ).exclude(pk=reservation.demandeur_id))


# --- Tâche planifiée ---

def marquer_terminees():
    """
    VALIDEE -> TERMINEE une fois le créneau passé. Lancée périodiquement
    (commande `cloturer_reservations`, via cron ou le workflow n8n). Les
    réservations en attente dont le créneau est passé sont refusées : elles
    ne pourront plus être honorées.
    """
    maintenant = timezone.localtime()
    passe = Q(date__lt=maintenant.date()) | Q(date=maintenant.date(), heure_fin__lte=maintenant.time())
    terminees = Reservation.objects.filter(passe, statut=StatutReservation.VALIDEE).update(
        statut=StatutReservation.TERMINEE
    )
    debut_passe = Q(date__lt=maintenant.date()) | Q(date=maintenant.date(), heure_debut__lte=maintenant.time())
    expirees = Reservation.objects.filter(debut_passe, statut=StatutReservation.EN_ATTENTE).update(
        statut=StatutReservation.REFUSEE, motif_refus="Demande non traitée avant le début du créneau.",
    )
    AlerteCreneau.objects.filter(active=True, date__lt=maintenant.date()).update(active=False)
    return terminees, expirees


def duree_heures(reservation):
    debut = datetime.combine(reservation.date, reservation.heure_debut)
    fin = datetime.combine(reservation.date, reservation.heure_fin)
    return (fin - debut).total_seconds() / 3600
