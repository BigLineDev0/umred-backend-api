"""
Changement de marque : l'organisation par défaut créée lors du passage au
SaaS (0002) s'appelait « UMRED ». Elle prend le nom de la plateforme,
SenLab. Seul le nom d'origine est remplacé : un nom déjà personnalisé par
l'administrateur de l'établissement n'est pas touché.
"""
from django.db import migrations


def renommer(apps, schema_editor):
    Organisation = apps.get_model('organisations', 'Organisation')
    Organisation.objects.filter(slug='umred', nom='UMRED').update(nom='SenLab')


def restaurer(apps, schema_editor):
    Organisation = apps.get_model('organisations', 'Organisation')
    Organisation.objects.filter(slug='umred', nom='SenLab').update(nom='UMRED')


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0002_organisation_par_defaut'),
    ]

    operations = [
        migrations.RunPython(renommer, restaurer),
    ]
