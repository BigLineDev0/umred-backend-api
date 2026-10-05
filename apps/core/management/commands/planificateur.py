import logging
import signal
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

INTERVALLE_CLOTURE = 15 * 60  # secondes
JOUR_SYNTHESE, HEURE_SYNTHESE = 0, 7  # lundi, 7h (heure de Dakar)


class Command(BaseCommand):
    help = (
        "Planificateur intégré pour Docker : clôture les réservations passées toutes les "
        "15 minutes et envoie la synthèse hebdomadaire le lundi à 7h. Alternative au cron "
        "ou au workflow n8n ; n'en activer qu'un seul pour éviter les envois en double."
    )

    def handle(self, *args, **options):
        actif = {'valeur': True}

        def arreter(*_):
            actif['valeur'] = False
        # Arrêt propre sur « docker compose stop » (SIGTERM).
        signal.signal(signal.SIGTERM, arreter)
        signal.signal(signal.SIGINT, arreter)

        derniere_cloture = 0.0
        derniere_synthese = None  # date du dernier envoi
        self.stdout.write('Planificateur démarré.')

        while actif['valeur']:
            maintenant = timezone.localtime()
            if time.monotonic() - derniere_cloture >= INTERVALLE_CLOTURE:
                self._executer('cloturer_reservations')
                derniere_cloture = time.monotonic()
            if (maintenant.weekday() == JOUR_SYNTHESE and maintenant.hour == HEURE_SYNTHESE
                    and derniere_synthese != maintenant.date()):
                self._executer('envoyer_synthese_hebdomadaire')
                derniere_synthese = maintenant.date()
            time.sleep(30)
        self.stdout.write('Planificateur arrêté.')

    def _executer(self, commande):
        # Une erreur (base indisponible un instant...) ne doit pas arrêter le planificateur.
        try:
            call_command(commande, stdout=self.stdout)
        except Exception:
            logger.exception('Échec de la tâche %s', commande)
