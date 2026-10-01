from dataclasses import dataclass


@dataclass
class AlerteUsure:
    niveau: str  # 'info', 'attention', 'critique'
    message: str
    heures_cumulees: float
    seuil: int
    pannes_recentes: int


def evaluer_usure(equipement) -> AlerteUsure | None:
    """
    Algorithme à seuils, déterministe et explicable : pas de modèle
    entraîné, juste deux règles métier combinées. Choix assumé pour la
    V1 — un vrai modèle prédictif (par exemple une régression sur
    l'historique de pannes) serait l'évolution naturelle si assez de
    données étaient accumulées sur plusieurs années d'usage réel.
    """
    # Règles évaluées de la plus grave à la moins grave ; la première qui
    # s'applique l'emporte :
    #   critique  : >= 2 pannes en 90 jours, OU heures >= 100 % du seuil
    #   attention : heures >= 80 % du seuil, OU 1 panne récente
    #   sinon     : pas d'alerte (None)
    heures = equipement.heures_utilisation_depuis_derniere_maintenance()
    seuil = equipement.seuil_heures_maintenance
    pannes = equipement.pannes_signalees_recentes()

    ratio_usure = heures / seuil if seuil else 0

    if pannes >= 2:
        return AlerteUsure(
            niveau='critique',
            message=f"{pannes} pannes signalées sur les 90 derniers jours, une maintenance préventive est fortement recommandée avant la prochaine panne.",
            heures_cumulees=heures, seuil=seuil, pannes_recentes=pannes,
        )
    if ratio_usure >= 1.0:
        return AlerteUsure(
            niveau='critique',
            message=f"{heures} heures d'utilisation depuis la dernière maintenance, le seuil de {seuil}h est dépassé.",
            heures_cumulees=heures, seuil=seuil, pannes_recentes=pannes,
        )
    if ratio_usure >= 0.8 or pannes == 1:
        return AlerteUsure(
            niveau='attention',
            message=f"{heures} heures d'utilisation sur {seuil}h avant maintenance recommandée, planifiez une intervention prochainement.",
            heures_cumulees=heures, seuil=seuil, pannes_recentes=pannes,
        )
    return None

# --- Statistiques d'utilisation et maintenance prédictive ---

def _duree_h(r):
    from datetime import datetime
    return (datetime.combine(r.date, r.heure_fin) - datetime.combine(r.date, r.heure_debut)).total_seconds() / 3600


def _jours_ouvres(debut, fin):
    """Nombre de jours du lundi au vendredi dans [debut, fin[."""
    from datetime import timedelta
    n, jour = 0, debut
    while jour < fin:
        if jour.weekday() < 5:
            n += 1
        jour += timedelta(days=1)
    return n


def statistiques_equipement(equipement, jours=30):
    """
    Tableau de bord d'un équipement : usage, fiabilité et prévision.

    Fiabilité, indicateurs classiques de la maintenance industrielle :
      - MTBF (Mean Time Between Failures) : intervalle moyen entre deux
        pannes signalées ; plus il est court, moins l'équipement est fiable ;
      - MTTR (Mean Time To Repair) : durée moyenne entre le signalement
        d'une panne et sa clôture ; mesure la réactivité de la maintenance.

    Prévision : au rythme d'utilisation des 30 derniers jours, dans combien
    de jours le seuil d'heures avant maintenance préventive sera atteint.
    C'est une projection linéaire, volontairement simple et explicable.
    """
    from collections import Counter, defaultdict
    from datetime import datetime, timedelta
    from django.utils import timezone
    from apps.maintenances.models import StatutMaintenance, TypeMaintenance
    from apps.organisations.isolation import regles_reservation
    from apps.reservations.models import StatutReservation

    maintenant = timezone.localtime()
    aujourd_hui = maintenant.date()
    regles = regles_reservation(equipement.laboratoire.organisation)
    acquises_statuts = [StatutReservation.VALIDEE, StatutReservation.TERMINEE]

    toutes = list(equipement.reservations.select_related('demandeur'))
    acquises = [r for r in toutes if r.statut in acquises_statuts]
    passees = [
        r for r in acquises
        if r.date < aujourd_hui or (r.date == aujourd_hui and r.heure_fin <= maintenant.time())
    ]
    a_venir = [r for r in acquises if r not in passees]

    # --- Usage ---
    debut_fenetre = aujourd_hui - timedelta(days=jours)
    recentes = [r for r in passees if r.date >= debut_fenetre]
    heures_recentes = sum(_duree_h(r) for r in recentes)
    amplitude_h = (
        datetime.combine(aujourd_hui, regles.heure_fermeture) - datetime.combine(aujourd_hui, regles.heure_ouverture)
    ).total_seconds() / 3600
    capacite_h = _jours_ouvres(debut_fenetre, aujourd_hui) * amplitude_h
    taux_occupation = round(100 * heures_recentes / capacite_h, 1) if capacite_h else 0

    par_statut = Counter(r.statut for r in toutes)
    utilisateurs = Counter(r.demandeur.nom_complet for r in acquises)

    # 12 derniers mois, du plus ancien au plus récent.
    mensuel = defaultdict(lambda: {'reservations': 0, 'heures': 0.0})
    for r in passees:
        cle = f'{r.date:%Y-%m}'
        mensuel[cle]['reservations'] += 1
        mensuel[cle]['heures'] += _duree_h(r)
    mois = []
    annee, m = aujourd_hui.year, aujourd_hui.month
    for _ in range(12):
        cle = f'{annee}-{m:02d}'
        mois.append({'mois': cle, 'reservations': mensuel[cle]['reservations'],
                     'heures': round(mensuel[cle]['heures'], 1)})
        m -= 1
        if m == 0:
            annee, m = annee - 1, 12
    mois.reverse()

    # Heures de pointe : créneaux horaires (jour de semaine × heure) les plus demandés.
    creneaux = Counter()
    for r in acquises:
        for heure in range(r.heure_debut.hour, r.heure_fin.hour + (1 if r.heure_fin.minute else 0)):
            creneaux[(r.date.weekday(), heure)] += 1
    jours_noms = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
    heures_de_pointe = [
        {'jour': jours_noms[j], 'heure': f'{h}h', 'reservations': n}
        for (j, h), n in creneaux.most_common(3)
    ]

    # --- Fiabilité ---
    maintenances = list(equipement.maintenances.all())
    correctives = sorted((mt for mt in maintenances if mt.type == TypeMaintenance.CORRECTIVE),
                         key=lambda mt: mt.date_creation)
    reparees = [mt for mt in correctives if mt.statut == StatutMaintenance.TERMINEE and mt.date_fin]
    mttr_h = round(sum((mt.date_fin - mt.date_creation).total_seconds() for mt in reparees) / len(reparees) / 3600, 1) if reparees else None
    intervalles = [(b.date_creation - a.date_creation).days for a, b in zip(correctives, correctives[1:])]
    mtbf_j = round(sum(intervalles) / len(intervalles), 1) if intervalles else None
    terminees = sorted((mt for mt in maintenances if mt.statut == StatutMaintenance.TERMINEE and mt.date_fin),
                       key=lambda mt: mt.date_fin)
    prochaines = sorted((mt for mt in maintenances if mt.statut == StatutMaintenance.PLANIFIEE and mt.date_planifiee >= timezone.now()),
                        key=lambda mt: mt.date_planifiee)

    # --- Prévision ---
    heures_depuis = equipement.heures_utilisation_depuis_derniere_maintenance()
    seuil = equipement.seuil_heures_maintenance
    rythme_h_jour = heures_recentes / jours if jours else 0
    prevision = None
    if seuil:
        restantes = max(seuil - heures_depuis, 0)
        if restantes == 0:
            prevision = {'date': aujourd_hui.isoformat(), 'jours': 0,
                         'message': "Seuil d'heures atteint : une maintenance préventive est due."}
        elif rythme_h_jour > 0:
            jours_restants = int(restantes / rythme_h_jour)
            date_prevue = aujourd_hui + timedelta(days=jours_restants)
            prevision = {
                'date': date_prevue.isoformat(), 'jours': jours_restants,
                'message': (f"Au rythme actuel ({rythme_h_jour:.1f} h/jour), le seuil de {seuil} h sera atteint "
                            f"vers le {date_prevue:%d/%m/%Y}. Planifiez la maintenance avant cette date."),
            }
        else:
            prevision = {'date': None, 'jours': None,
                         'message': "Pas d'utilisation récente : aucune maintenance d'usure à prévoir pour l'instant."}

    # Score de santé 0-100 : part d'usure consommée et pannes récentes.
    pannes_recentes = equipement.pannes_signalees_recentes()
    ratio = min(heures_depuis / seuil, 1.5) if seuil else 0
    score = max(0, round(100 - ratio * 50 - pannes_recentes * 20))
    etat = 'bon' if score >= 70 else 'surveiller' if score >= 40 else 'critique'

    return {
        'usage': {
            'reservations_total': len(toutes),
            'reservations_par_statut': dict(par_statut),
            'reservations_a_venir': len(a_venir),
            'heures_totales': round(sum(_duree_h(r) for r in passees), 1),
            'heures_periode': round(heures_recentes, 1),
            'periode_jours': jours,
            'taux_occupation': taux_occupation,
            'taux_annulation': round(100 * par_statut.get(StatutReservation.ANNULEE, 0) / len(toutes), 1) if toutes else 0,
            'utilisateurs_distincts': len(utilisateurs),
            'top_utilisateurs': [{'nom': nom, 'reservations': n} for nom, n in utilisateurs.most_common(5)],
            'mensuel': mois,
            'heures_de_pointe': heures_de_pointe,
        },
        'fiabilite': {
            'maintenances_total': len(maintenances),
            'pannes_total': len(correctives),
            'pannes_90_jours': pannes_recentes,
            'mtbf_jours': mtbf_j,
            'mttr_heures': mttr_h,
            'derniere_maintenance': terminees[-1].date_fin.date().isoformat() if terminees else None,
            'prochaine_maintenance': prochaines[0].date_planifiee.date().isoformat() if prochaines else None,
        },
        'prevision': {
            'heures_depuis_maintenance': heures_depuis,
            'seuil_heures': seuil,
            'usure_pourcentage': round(100 * heures_depuis / seuil, 1) if seuil else 0,
            'maintenance_estimee': prevision,
        },
        'sante': {'score': score, 'etat': etat},
    }


def reservations_impactees(equipement, jusqu_au=None, depuis=None):
    """Réservations à venir (acquises ou en attente) qu'une indisponibilité compromet."""
    from django.utils import timezone
    from apps.reservations.models import StatutReservation

    depuis = depuis or timezone.localdate()
    qs = equipement.reservations.filter(
        date__gte=depuis, statut__in=[StatutReservation.VALIDEE, StatutReservation.EN_ATTENTE],
    ).select_related('demandeur', 'laboratoire').order_by('date', 'heure_debut')
    if jusqu_au:
        qs = qs.filter(date__lte=jusqu_au)
    return list(qs)


def prevenir_indisponibilite(equipement, reservations, raison):
    """
    Prévient chaque demandeur concerné et lui propose un équipement
    équivalent libre sur son créneau, s'il en existe un. La réservation
    n'est pas annulée d'office : c'est au technicien ou au demandeur d'en
    décider (la réparation peut être rapide).
    """
    from apps.notifications.models import TypeNotification
    from apps.notifications.services import notifier
    from apps.reservations.services import equipements_equivalents

    for r in reservations:
        conflit = [{'equipement_id': equipement.id}]
        equivalents = equipements_equivalents(r.laboratoire, [equipement], conflit, r.date, r.heure_debut, r.heure_fin)
        suggestion = f" Équipement équivalent disponible sur votre créneau : {equivalents[0]['nom']}." if equivalents else ''
        notifier(
            r.demandeur, f'{equipement.nom} indisponible',
            f"{raison} Votre réservation du {r.date:%d/%m/%Y} ({r.heure_debut:%H:%M}-{r.heure_fin:%H:%M}) "
            f"risque d'être compromise.{suggestion}",
            TypeNotification.MAINTENANCE, r, email=True,
        )
    return len(reservations)
