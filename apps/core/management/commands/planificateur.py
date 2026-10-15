import logging
import signal
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

INTERVALLE_RAPPELS = 5 * 60   # secondes
INTERVALLE_CLOTURE = 15 * 60  # secondes


class Command(BaseCommand):
    help = (
        "Planificateur intégré pour Docker : envoie les rappels de réservation toutes les 5 minutes "
        "et clôture les réservations passées toutes les 15 minutes. La synthèse hebdomadaire est "
        "orchestrée par n8n (voir deploiement/n8n/)."
    )

    def handle(self, *args, **options):
        actif = {'valeur': True}

        def arreter(*_):
            actif['valeur'] = False
        # Arrêt propre sur « docker compose stop » (SIGTERM).
        signal.signal(signal.SIGTERM, arreter)
        signal.signal(signal.SIGINT, arreter)

        dernieres = {'envoyer_rappels_reservations': 0.0, 'cloturer_reservations': 0.0}
        intervalles = {'envoyer_rappels_reservations': INTERVALLE_RAPPELS, 'cloturer_reservations': INTERVALLE_CLOTURE}
        self.stdout.write('Planificateur démarré.')

        while actif['valeur']:
            for commande, intervalle in intervalles.items():
                if time.monotonic() - dernieres[commande] >= intervalle:
                    self._executer(commande)
                    dernieres[commande] = time.monotonic()
            time.sleep(30)
        self.stdout.write('Planificateur arrêté.')

    def _executer(self, commande):
        # Une erreur (base indisponible un instant...) ne doit pas arrêter le planificateur.
        try:
            call_command(commande, stdout=self.stdout)
        except Exception:
            logger.exception('Échec de la tâche %s', commande)
