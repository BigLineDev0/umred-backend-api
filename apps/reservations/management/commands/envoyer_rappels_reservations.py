from django.core.management.base import BaseCommand

from apps.reservations.rappels import envoyer_rappels


class Command(BaseCommand):
    help = ("Envoie les rappels de réservation (24 h et 1 h avant le créneau) par notification et email. "
            "Lancée toutes les 5 minutes par le planificateur.")

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f'{envoyer_rappels()} rappel(s) envoyé(s).'))
