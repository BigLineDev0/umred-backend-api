from django.db import models
from apps.laboratoires.models import Laboratoire


class StatutEquipement(models.TextChoices):
    DISPONIBLE = 'DISPONIBLE', 'Disponible'
    RESERVE = 'RESERVE', 'Réservé'
    EN_MAINTENANCE = 'EN_MAINTENANCE', 'En maintenance'
    EN_PANNE = 'EN_PANNE', 'En panne'
    HORS_SERVICE = 'HORS_SERVICE', 'Hors service'


class Equipement(models.Model):
    laboratoire = models.ForeignKey(Laboratoire, on_delete=models.CASCADE, related_name='equipements')
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    marque = models.CharField(max_length=100, blank=True)
    modele = models.CharField(max_length=100, blank=True)
    numero_serie = models.CharField(max_length=100, unique=True)
    date_acquisition = models.DateField(null=True, blank=True)
    statut = models.CharField(max_length=20, choices=StatutEquipement.choices, default=StatutEquipement.DISPONIBLE)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Équipement'
        verbose_name_plural = 'Équipements'
        ordering = ['nom']

    def __str__(self):
        return f'{self.nom} ({self.numero_serie})'

    def verifier_disponibilite(self):
        return self.statut == StatutEquipement.DISPONIBLE

    def changer_statut(self, nouveau_statut):
        self.statut = nouveau_statut
        self.save(update_fields=['statut'])