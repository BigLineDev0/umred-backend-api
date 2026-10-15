"""
Synthèse hebdomadaire envoyée aux administrateurs, orchestrée par n8n
(workflow deploiement/n8n/synthese-hebdomadaire.json) :

1. n8n lit ici, pour chaque établissement actif, les indicateurs de la
   semaine écoulée et les administrateurs destinataires ;
2. le service IA rédige la synthèse à partir de ces indicateurs (chaque
   chiffre du texte est vérifié, repli sur les gabarits sinon) ;
3. n8n demande ici le rapport PDF (synthèse en tête + indicateurs
   détaillés), ce qui notifie aussi les administrateurs dans l'application ;
4. n8n envoie le PDF par email.

Django reste la source des chiffres : le texte reçu de n8n n'est qu'une
rédaction, acceptée seulement s'il a la forme attendue.
"""
from datetime import timedelta

from django.utils import timezone

from apps.notifications.models import TypeNotification
from apps.notifications.services import notifier
from apps.organisations.models import Organisation
from apps.reservations.models import Reservation
from apps.utilisateurs.models import Role, StatutCompte, Utilisateur

from .analytique import calculer_indicateurs
from .rapport_pdf import generer_rapport_pdf

# Une synthèse de 4 à 6 phrases tient largement dans cette limite : au-delà,
# le texte reçu est jugé anormal et remplacé par la version par gabarits.
LONGUEUR_MAX_SYNTHESE = 3000


def synthese_valide(texte):
    """Texte non vide et de taille bornée, ou None."""
    if not isinstance(texte, str):
        return None
    texte = texte.strip()
    return texte if 0 < len(texte) <= LONGUEUR_MAX_SYNTHESE else None


def periode_semaine_ecoulee():
    """Les 7 derniers jours complets (hier inclus)."""
    fin = timezone.localdate() - timedelta(days=1)
    return fin - timedelta(days=6), fin


def administrateurs(organisation):
    return Utilisateur.objects.filter(
        organisation=organisation, role=Role.ADMIN, statut_compte=StatutCompte.ACTIF,
    ).order_by('id')


def _indicateurs(organisation):
    # calculer_indicateurs limite ses calculs à l'établissement de
    # l'utilisateur passé : un administrateur de l'établissement suffit.
    admin = administrateurs(organisation).first()
    if admin is None:
        return None
    debut, fin = periode_semaine_ecoulee()
    return calculer_indicateurs(admin, debut, fin)


def etablissements_a_synthetiser():
    """Une entrée par établissement actif ayant au moins un administrateur joignable."""
    resultat = []
    for organisation in Organisation.objects.filter(est_active=True).order_by('nom'):
        destinataires = [{'email': a.email, 'prenom': a.prenom, 'nom': a.nom}
                         for a in administrateurs(organisation) if a.email]
        if not destinataires:
            continue
        indicateurs = _indicateurs(organisation)
        resultat.append({
            'organisation_id': organisation.id,
            'organisation': organisation.nom,
            'periode': indicateurs['periode'],
            'indicateurs': indicateurs,
            'destinataires': destinataires,
        })
    return resultat


def rapport_synthese(organisation, synthese):
    """
    Renvoie (contenu PDF, nom de fichier) du rapport de la semaine écoulée,
    ou None si l'établissement n'a pas d'administrateur actif. Notifie les
    administrateurs dans l'application avec le texte retenu.
    """
    indicateurs = _indicateurs(organisation)
    if indicateurs is None:
        return None
    texte = synthese_valide(synthese) or indicateurs['synthese_regles']
    debut, fin = periode_semaine_ecoulee()
    reservations = list(
        Reservation.objects.filter(laboratoire__organisation=organisation, date__gte=debut, date__lte=fin)
        .exclude(est_archivee=True)
        .select_related('laboratoire', 'demandeur', 'validateur').prefetch_related('equipements')
        .order_by('date', 'heure_debut')
    )
    contenu = generer_rapport_pdf(indicateurs, reservations, organisation, synthese=texte)
    for admin in administrateurs(organisation):
        notifier(admin, f'Synthèse de la semaine — {organisation.nom}',
                 f'{texte}\n\nLe rapport PDF détaillé vous est envoyé par email.', TypeNotification.SYSTEME)
    return contenu, f'synthese_{organisation.slug}_{fin:%Y%m%d}.pdf'
