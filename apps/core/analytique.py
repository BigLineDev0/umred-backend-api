"""
Moteur d'aide à la décision : transforme l'historique des réservations et
des maintenances en indicateurs et en RECOMMANDATIONS actionnables pour
l'administrateur et les techniciens.

Choix assumé : des statistiques et des règles explicables plutôt qu'un
modèle opaque. Chaque recommandation cite les chiffres qui la justifient,
ce qui permet à un responsable de laboratoire de la vérifier et de la
défendre (achat d'un second équipement, extension d'horaires...).
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from django.utils import timezone

from apps.consommables.models import Consommable
from apps.equipements.models import Equipement
from apps.equipements.services import evaluer_usure
from apps.laboratoires.models import Laboratoire
from apps.organisations.isolation import filtrer_par_organisation, regles_reservation
from apps.reservations.models import Reservation, StatutReservation, STATUTS_BLOQUANTS

JOURS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
SEUIL_SATURATION = 75  # % d'occupation au-delà duquel les demandes risquent d'être refusées
SEUIL_SOUS_UTILISATION = 10  # % en dessous duquel un équipement est considéré comme sous-utilisé


def _duree_h(r):
    return (datetime.combine(r.date, r.heure_fin) - datetime.combine(r.date, r.heure_debut)).total_seconds() / 3600


def _jours_ouvres(debut, fin):
    n, jour = 0, debut
    while jour <= fin:
        if jour.weekday() < 5:
            n += 1
        jour += timedelta(days=1)
    return n


def calculer_indicateurs(user, date_debut=None, date_fin=None, laboratoire_id=None):
    aujourd_hui = timezone.localdate()
    date_fin = date_fin or aujourd_hui
    date_debut = date_debut or (date_fin - timedelta(days=29))
    organisation = user.organisation
    regles = regles_reservation(organisation)
    amplitude_h = (
        datetime.combine(aujourd_hui, regles.heure_fermeture) - datetime.combine(aujourd_hui, regles.heure_ouverture)
    ).total_seconds() / 3600
    capacite_par_ressource = _jours_ouvres(date_debut, date_fin) * amplitude_h

    reservations = filtrer_par_organisation(Reservation.objects.all(), user, 'laboratoire__organisation').filter(
        date__gte=date_debut, date__lte=date_fin,
    ).exclude(est_archivee=True)
    laboratoires = filtrer_par_organisation(Laboratoire.objects.all(), user)
    equipements = filtrer_par_organisation(Equipement.objects.all(), user, 'laboratoire__organisation')
    if laboratoire_id:
        reservations = reservations.filter(laboratoire_id=laboratoire_id)
        laboratoires = laboratoires.filter(id=laboratoire_id)
        equipements = equipements.filter(laboratoire_id=laboratoire_id)
    reservations = list(reservations.select_related('laboratoire', 'demandeur').prefetch_related('equipements'))
    acquises = [r for r in reservations if r.statut in STATUTS_BLOQUANTS]

    # --- Volumes et décisions ---
    par_statut = Counter(r.statut for r in reservations)
    total = len(reservations)
    decidees = [r for r in reservations if r.date_validation and r.validateur_id]
    delais_h = [(r.date_validation - r.date_creation).total_seconds() / 3600 for r in decidees]
    delai_moyen_h = round(sum(delais_h) / len(delais_h), 1) if delais_h else None
    en_attente_anciennes = filtrer_par_organisation(Reservation.objects.all(), user, 'laboratoire__organisation').filter(
        statut=StatutReservation.EN_ATTENTE, date_creation__lt=timezone.now() - timedelta(hours=48),
    ).count()

    # --- Occupation ---
    heures_par_labo = defaultdict(float)
    heures_par_equipement = defaultdict(float)
    for r in acquises:
        heures_par_labo[r.laboratoire_id] += _duree_h(r)
        for e in r.equipements.all():
            heures_par_equipement[e.id] += _duree_h(r)

    occupation_labos = sorted([
        {
            'id': l.id, 'nom': l.nom,
            'heures': round(heures_par_labo[l.id], 1),
            'taux_occupation': round(100 * heures_par_labo[l.id] / capacite_par_ressource, 1) if capacite_par_ressource else 0,
        } for l in laboratoires
    ], key=lambda x: -x['taux_occupation'])

    occupation_equipements = sorted([
        {
            'id': e.id, 'nom': e.nom, 'laboratoire': e.laboratoire.nom, 'categorie': e.categorie,
            'heures': round(heures_par_equipement[e.id], 1),
            'taux_occupation': round(100 * heures_par_equipement[e.id] / capacite_par_ressource, 1) if capacite_par_ressource else 0,
        } for e in equipements.select_related('laboratoire').exclude(statut='HORS_SERVICE')
    ], key=lambda x: -x['taux_occupation'])

    # --- Heures de pointe : matrice jour ouvré × heure d'ouverture ---
    heures = list(range(regles.heure_ouverture.hour, regles.heure_fermeture.hour + (1 if regles.heure_fermeture.minute else 0)))
    matrice = {(j, h): 0 for j in range(5) for h in heures}
    for r in acquises:
        if r.date.weekday() >= 5:
            continue
        for h in range(r.heure_debut.hour, r.heure_fin.hour + (1 if r.heure_fin.minute else 0)):
            if (r.date.weekday(), h) in matrice:
                matrice[(r.date.weekday(), h)] += 1
    carte_chaleur = [
        {'jour': JOURS[j], 'valeurs': [matrice[(j, h)] for h in heures]} for j in range(5)
    ]
    pics = sorted(matrice.items(), key=lambda kv: -kv[1])[:3]
    heures_de_pointe = [{'jour': JOURS[j], 'heure': f'{h}h', 'reservations': n} for (j, h), n in pics if n]

    # --- Prévision de la semaine prochaine ---
    # Moyenne des 4 dernières semaines pour chaque jour ouvré : estimation
    # simple et robuste de la charge attendue (« moyenne saisonnière »).
    historique = filtrer_par_organisation(Reservation.objects.all(), user, 'laboratoire__organisation').filter(
        date__gte=aujourd_hui - timedelta(days=28), date__lt=aujourd_hui, statut__in=STATUTS_BLOQUANTS,
    )
    if laboratoire_id:
        historique = historique.filter(laboratoire_id=laboratoire_id)
    par_jour = Counter(d.weekday() for d in historique.values_list('date', flat=True))
    lundi_prochain = aujourd_hui + timedelta(days=7 - aujourd_hui.weekday())
    deja_planifiees = Counter(
        d.weekday() for d in filtrer_par_organisation(Reservation.objects.all(), user, 'laboratoire__organisation').filter(
            date__gte=lundi_prochain, date__lt=lundi_prochain + timedelta(days=5),
            statut__in=STATUTS_BLOQUANTS + [StatutReservation.EN_ATTENTE],
        ).values_list('date', flat=True)
    )
    prevision = [
        {
            'jour': JOURS[j], 'date': (lundi_prochain + timedelta(days=j)).isoformat(),
            'attendues': round(par_jour[j] / 4, 1), 'deja_planifiees': deja_planifiees[j],
        } for j in range(5)
    ]

    # --- Alertes matériel et stock ---
    alertes_usure = []
    for e in equipements.exclude(statut='HORS_SERVICE').select_related('laboratoire'):
        alerte = evaluer_usure(e)
        if alerte:
            alertes_usure.append({'equipement': e.nom, 'niveau': alerte.niveau, 'message': alerte.message})
    consommables = filtrer_par_organisation(Consommable.objects.all(), user, 'laboratoire__organisation')
    if laboratoire_id:
        consommables = consommables.filter(laboratoire_id=laboratoire_id)
    alertes_stock = [c.nom for c in consommables if c.statut in ['STOCK_FAIBLE', 'EPUISE', 'PERIME']]

    indicateurs = {
        'periode': {'debut': date_debut.isoformat(), 'fin': date_fin.isoformat()},
        'organisation': organisation.nom if organisation else None,
        'volumes': {
            'reservations': total,
            'validees': par_statut.get(StatutReservation.VALIDEE, 0) + par_statut.get(StatutReservation.TERMINEE, 0),
            'en_attente': par_statut.get(StatutReservation.EN_ATTENTE, 0),
            'refusees': par_statut.get(StatutReservation.REFUSEE, 0),
            'annulees': par_statut.get(StatutReservation.ANNULEE, 0),
            'taux_annulation': round(100 * par_statut.get(StatutReservation.ANNULEE, 0) / total, 1) if total else 0,
            'taux_refus': round(100 * par_statut.get(StatutReservation.REFUSEE, 0) / total, 1) if total else 0,
            'heures_utilisation': round(sum(_duree_h(r) for r in acquises), 1),
            'utilisateurs_actifs': len({r.demandeur_id for r in reservations}),
            'delai_moyen_validation_h': delai_moyen_h,
            'demandes_en_attente_48h': en_attente_anciennes,
        },
        'occupation_laboratoires': occupation_labos,
        'occupation_equipements': occupation_equipements,
        'carte_chaleur': {'heures': [f'{h}h' for h in heures], 'jours': carte_chaleur},
        'heures_de_pointe': heures_de_pointe,
        'prevision_semaine': prevision,
        'alertes_usure': alertes_usure,
        'alertes_stock': alertes_stock,
    }
    indicateurs['recommandations'] = recommandations(indicateurs, date_fin - date_debut)
    indicateurs['synthese_regles'] = synthese_par_regles(indicateurs)
    return indicateurs


def synthese_par_regles(ind):
    """
    Synthèse rédigée par des gabarits : toujours disponible, et utilisée
    comme repli quand le modèle de langage est indisponible ou que sa
    réponse ne passe pas le contrôle anti-hallucination du service IA.
    """
    v = ind['volumes']
    phrases = [
        f"Du {ind['periode']['debut']} au {ind['periode']['fin']}, {v['reservations']} réservations ont été "
        f"enregistrées pour {v['heures_utilisation']} heures d'utilisation, par {v['utilisateurs_actifs']} utilisateurs."
    ]
    if ind['occupation_equipements']:
        top = ind['occupation_equipements'][0]
        phrases.append(f"L'équipement le plus sollicité est {top['nom']} ({top['taux_occupation']} % d'occupation).")
    if ind['heures_de_pointe']:
        pic = ind['heures_de_pointe'][0]
        phrases.append(f"Le pic d'activité se situe le {pic['jour'].lower()} à {pic['heure']}.")
    if v['delai_moyen_validation_h'] is not None:
        phrases.append(f"Les demandes sont traitées en {v['delai_moyen_validation_h']} heures en moyenne.")
    prioritaires = [r for r in ind['recommandations'] if r['niveau'] != 'info']
    if prioritaires:
        phrases.append("Points d'attention : " + ' ; '.join(r['titre'] for r in prioritaires[:3]) + '.')
    else:
        phrases.append("Aucun point d'attention majeur cette semaine.")
    return ' '.join(phrases)


def recommandations(ind, duree_periode):
    """Règles d'expertise : chaque recommandation cite les chiffres qui la motivent."""
    recos = []
    v = ind['volumes']

    for e in ind['occupation_equipements']:
        if e['taux_occupation'] >= SEUIL_SATURATION:
            recos.append({
                'niveau': 'critique', 'categorie': 'capacite',
                'titre': f"{e['nom']} est saturé",
                'message': (f"Occupé à {e['taux_occupation']} % des heures d'ouverture ({e['heures']} h). "
                            "Les demandes risquent d'être refusées : envisagez un équipement supplémentaire "
                            "de la même catégorie ou une extension des horaires."),
            })
    # La sous-utilisation n'a de sens que sur une période assez longue.
    if duree_periode >= timedelta(days=13):
        sous_utilises = [e for e in ind['occupation_equipements'] if e['taux_occupation'] < SEUIL_SOUS_UTILISATION]
        if sous_utilises:
            noms = ', '.join(e['nom'] for e in sous_utilises[:5])
            recos.append({
                'niveau': 'info', 'categorie': 'capacite',
                'titre': f"{len(sous_utilises)} équipement(s) sous-utilisé(s)",
                'message': (f"Moins de {SEUIL_SOUS_UTILISATION} % d'occupation : {noms}. Faites-les connaître aux "
                            "équipes, mutualisez-les avec un autre laboratoire ou réaffectez-les."),
            })

    if ind['heures_de_pointe']:
        pic = ind['heures_de_pointe'][0]
        recos.append({
            'niveau': 'info', 'categorie': 'planification',
            'titre': f"Pic de demande le {pic['jour'].lower()} à {pic['heure']}",
            'message': ("Évitez d'y planifier des maintenances préventives et incitez les utilisateurs "
                        "à réserver sur les créneaux creux."),
        })

    if v['delai_moyen_validation_h'] and v['delai_moyen_validation_h'] > 24:
        recos.append({
            'niveau': 'attention', 'categorie': 'processus',
            'titre': 'Validation trop lente',
            'message': (f"Les demandes attendent en moyenne {v['delai_moyen_validation_h']} h une décision. "
                        "Rattachez les étudiants à un encadrant pour répartir la charge de validation."),
        })
    if v['demandes_en_attente_48h']:
        recos.append({
            'niveau': 'attention', 'categorie': 'processus',
            'titre': f"{v['demandes_en_attente_48h']} demande(s) en attente depuis plus de 48 h",
            'message': "Traitez-les depuis la file d'attente : elles expireront au début de leur créneau.",
        })
    if v['reservations'] >= 10 and v['taux_annulation'] >= 20:
        recos.append({
            'niveau': 'attention', 'categorie': 'usage',
            'titre': "Taux d'annulation élevé",
            'message': (f"{v['taux_annulation']} % des réservations sont annulées : des créneaux sont bloqués "
                        "pour rien. Les rappels 24 h / 1 h avant sont-ils bien activés ?"),
        })

    charge = max(ind['prevision_semaine'], key=lambda j: j['attendues'], default=None)
    if charge and charge['attendues'] >= 1:
        recos.append({
            'niveau': 'info', 'categorie': 'prevision',
            'titre': f"Semaine prochaine : {charge['jour'].lower()} sera le jour le plus chargé",
            'message': (f"Environ {charge['attendues']} réservations attendues d'après les 4 dernières semaines "
                        f"({charge['deja_planifiees']} déjà planifiées). Prévoyez un technicien disponible."),
        })

    critiques = [a for a in ind['alertes_usure'] if a['niveau'] == 'critique']
    if critiques:
        recos.append({
            'niveau': 'critique', 'categorie': 'maintenance',
            'titre': f"{len(critiques)} équipement(s) à entretenir en priorité",
            'message': ' '.join(f"{a['equipement']} : {a['message']}" for a in critiques[:3]),
        })
    if ind['alertes_stock']:
        recos.append({
            'niveau': 'attention', 'categorie': 'stock',
            'titre': f"{len(ind['alertes_stock'])} consommable(s) à réapprovisionner",
            'message': ', '.join(ind['alertes_stock'][:8]) + '.',
        })

    ordre = {'critique': 0, 'attention': 1, 'info': 2}
    return sorted(recos, key=lambda r: ordre[r['niveau']])
