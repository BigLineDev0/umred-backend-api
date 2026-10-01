import logging
from datetime import timedelta

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.analytique import calculer_indicateurs
from apps.notifications.models import TypeNotification
from apps.notifications.services import notifier
from apps.organisations.models import Organisation
from apps.utilisateurs.models import Role, StatutCompte, Utilisateur

logger = logging.getLogger(__name__)


def rediger(admin, date_debut, date_fin):
    """
    Demande la synthèse au service IA avec un token d'accès émis pour
    l'admin : le service IA relit les indicateurs chez Django avec SES
    droits. En cas d'indisponibilité, repli sur la synthèse par règles.
    """
    try:
        reponse = httpx.get(
            f'{settings.IA_SERVICE_URL}/pilotage/synthese',
            params={'date_debut': date_debut.isoformat(), 'date_fin': date_fin.isoformat()},
            headers={'Authorization': f'Bearer {AccessToken.for_user(admin)}'},
            timeout=120,
        )
        reponse.raise_for_status()
        return reponse.json()['synthese']
    except Exception as exc:
        logger.warning('Service IA indisponible pour la synthèse (%s) : repli sur les règles.', exc)
        return calculer_indicateurs(admin, date_debut, date_fin)['synthese_regles']


def envoyer_syntheses():
    """Synthèse des 7 derniers jours à chaque admin actif ; renvoie le nombre envoyé."""
    date_fin = timezone.localdate() - timedelta(days=1)
    date_debut = date_fin - timedelta(days=6)
    envoyes = 0
    for organisation in Organisation.objects.filter(est_active=True):
        admins = Utilisateur.objects.filter(organisation=organisation, role=Role.ADMIN, statut_compte=StatutCompte.ACTIF)
        for admin in admins:
            synthese = rediger(admin, date_debut, date_fin)
            notifier(admin, f'Synthèse de la semaine — {organisation.nom}', synthese,
                     TypeNotification.SYSTEME, email=True)
            envoyes += 1
    return envoyes


class Command(BaseCommand):
    help = ("Envoie à chaque administrateur la synthèse de la semaine écoulée de son établissement "
            "(notification + email). À planifier le lundi matin (cron ou n8n).")

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f'{envoyer_syntheses()} synthèse(s) envoyée(s).'))
