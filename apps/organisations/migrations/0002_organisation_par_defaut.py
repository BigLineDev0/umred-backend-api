"""
Passage au SaaS : les données existantes (mono-établissement) sont
rattachées à une organisation par défaut, pour que l'isolation par
organisation ne les rende pas invisibles.
"""
from django.db import migrations


def rattacher(apps, schema_editor):
    Organisation = apps.get_model('organisations', 'Organisation')
    Utilisateur = apps.get_model('utilisateurs', 'Utilisateur')
    Laboratoire = apps.get_model('laboratoires', 'Laboratoire')
    if not Utilisateur.objects.exists() and not Laboratoire.objects.exists():
        return  # base neuve : rien à migrer
    organisation, _ = Organisation.objects.get_or_create(
        slug='umred', defaults={'nom': 'UMRED', 'ville': 'Dakar'},
    )
    Utilisateur.objects.filter(organisation__isnull=True).exclude(role='SUPER_ADMIN').update(organisation=organisation)
    Laboratoire.objects.filter(organisation__isnull=True).update(organisation=organisation)


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0001_initial'),
        ('utilisateurs', '0008_utilisateur_encadrant_utilisateur_organisation_and_more'),
        ('laboratoires', '0003_laboratoire_organisation'),
    ]

    operations = [
        migrations.RunPython(rattacher, migrations.RunPython.noop),
    ]
