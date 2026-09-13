from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class TypeNotification(models.TextChoices):
    RESERVATION = 'RESERVATION', 'Réservation'
    MAINTENANCE = 'MAINTENANCE', 'Maintenance'
    VALIDATION = 'VALIDATION', 'Validation'
    RAPPEL = 'RAPPEL', 'Rappel'
    SYSTEME = 'SYSTEME', 'Système'


class Notification(models.Model):
    destinataire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications'
    )
    titre = models.CharField(max_length=150)
    message = models.TextField()
    type = models.CharField(max_length=20, choices=TypeNotification.choices)
    lu = models.BooleanField(default=False)

    # Même mécanique que JournalActivite : pointe vers n'importe quel objet
    # (Reservation, Maintenance...) sans dupliquer de colonnes.
    entite_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    entite_id = models.PositiveIntegerField(null=True, blank=True)
    entite = GenericForeignKey('entite_type', 'entite_id')

    date_envoi = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
        ordering = ['-date_envoi']

    def __str__(self):
        return f'{self.titre} — {self.destinataire}'

    def marquer_comme_lue(self):
        self.lu = True
        self.save(update_fields=['lu'])