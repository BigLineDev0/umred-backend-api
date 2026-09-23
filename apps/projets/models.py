from django.conf import settings
from django.db import models


class NiveauPriorite(models.TextChoices):
    BASSE = 'BASSE', 'Basse'
    NORMALE = 'NORMALE', 'Normale'
    HAUTE = 'HAUTE', 'Haute'
    CRITIQUE = 'CRITIQUE', 'Critique'


RANG_PRIORITE = {
    NiveauPriorite.CRITIQUE: 4,
    NiveauPriorite.HAUTE: 3,
    NiveauPriorite.NORMALE: 2,
    NiveauPriorite.BASSE: 1,
}


class Projet(models.Model):
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='projets'
    )
    niveau_priorite = models.CharField(
        max_length=10, choices=NiveauPriorite.choices, default=NiveauPriorite.NORMALE
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Projet'
        ordering = ['-date_creation']

    def __str__(self):
        return self.nom