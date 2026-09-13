from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class JournalActivite(models.Model):
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='actions_journalisees'
    )
    action = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    # Référence générique : permet de pointer vers N'IMPORTE QUEL modèle
    # (Reservation, Maintenance, Utilisateur...) sans créer une table de
    # journal séparée pour chacun.
    entite_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    entite_id = models.PositiveIntegerField(null=True, blank=True)
    entite = GenericForeignKey('entite_type', 'entite_id')

    date_heure = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Journal d'activité"
        verbose_name_plural = "Journal d'activité"
        ordering = ['-date_heure']

    def __str__(self):
        return f'{self.date_heure:%d/%m/%Y %H:%M} — {self.auteur} — {self.action}'