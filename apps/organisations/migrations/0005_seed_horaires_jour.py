"""
Initialise les horaires par jour de chaque établissement existant à partir
de ses horaires globaux (tous les jours ouverts, mêmes heures). L'admin
pourra ensuite fermer un jour (ex. dimanche) ou changer une plage.
"""
from django.db import migrations


def seed(apps, schema_editor):
    Organisation = apps.get_model('organisations', 'Organisation')
    HoraireJour = apps.get_model('organisations', 'HoraireJour')
    for organisation in Organisation.objects.all():
        for jour in range(7):
            HoraireJour.objects.get_or_create(
                organisation=organisation, jour=jour,
                defaults={
                    'ferme': False,
                    'heure_ouverture': organisation.heure_ouverture,
                    'heure_fermeture': organisation.heure_fermeture,
                },
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('organisations', '0004_horairejour')]
    operations = [migrations.RunPython(seed, noop)]
