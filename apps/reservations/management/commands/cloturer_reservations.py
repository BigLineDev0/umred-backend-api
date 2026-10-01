from django.core.management.base import BaseCommand

from apps.reservations.services import marquer_terminees


class Command(BaseCommand):
    help = (
        "Passe en TERMINEE les réservations validées dont le créneau est passé, "
        "et refuse les demandes restées en attente après le début de leur créneau. "
        "À planifier toutes les 15 minutes (cron ou workflow n8n)."
    )

    def handle(self, *args, **options):
        terminees, expirees = marquer_terminees()
        self.stdout.write(self.style.SUCCESS(
            f"{terminees} réservation(s) terminée(s), {expirees} demande(s) expirée(s)."
        ))
