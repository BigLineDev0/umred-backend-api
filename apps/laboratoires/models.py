from django.db import models
from django.db.models.functions import Lower
from django.conf import settings


class StatutLaboratoire(models.TextChoices):
    DISPONIBLE = 'DISPONIBLE', 'Disponible'
    INDISPONIBLE = 'INDISPONIBLE', 'Indisponible'


class Laboratoire(models.Model):
    # Racine de l'isolation SaaS : équipements, réservations, maintenances
    # et consommables sont rattachés à l'organisation via leur laboratoire.
    organisation = models.ForeignKey(
        'organisations.Organisation', on_delete=models.PROTECT, null=True, blank=True,
        related_name='laboratoires'
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    localisation = models.CharField(max_length=150)
    capacite = models.PositiveIntegerField(null=True, blank=True)
    statut = models.CharField(
        max_length=20, choices=StatutLaboratoire.choices, default=StatutLaboratoire.DISPONIBLE
    )
    photo = models.URLField(blank=True, null=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='laboratoires_geres'
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Laboratoire'
        verbose_name_plural = 'Laboratoires'
        ordering = ['nom']
        constraints = [
            # Nom unique par organisation, insensible à la casse (backstop
            # base ; l'insensibilité aux accents est assurée par le serializer).
            models.UniqueConstraint(Lower('nom'), 'organisation', name='uniq_labo_nom_par_organisation'),
        ]

    def __str__(self):
        return self.nom

    def modifier(self, **champs):
        for champ, valeur in champs.items():
            setattr(self, champ, valeur)
        self.save()

    def ajouter_equipement(self, **champs_equipement):
        from apps.equipements.models import Equipement
        return Equipement.objects.create(laboratoire=self, **champs_equipement)