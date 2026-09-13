from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.equipements.models import Equipement, StatutEquipement


class TypeMaintenance(models.TextChoices):
    PREVENTIVE = 'PREVENTIVE', 'Préventive'
    CORRECTIVE = 'CORRECTIVE', 'Corrective'


class StatutMaintenance(models.TextChoices):
    PLANIFIEE = 'PLANIFIEE', 'Planifiée'
    EN_COURS = 'EN_COURS', 'En cours'
    TERMINEE = 'TERMINEE', 'Terminée'
    ANNULEE = 'ANNULEE', 'Annulée'


class Maintenance(models.Model):
    equipement = models.ForeignKey(
        Equipement, on_delete=models.CASCADE, related_name='maintenances'
    )
    technicien = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='maintenances_realisees'
    )
    signale_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pannes_signalees'
    )
    type = models.CharField(max_length=20, choices=TypeMaintenance.choices)
    description = models.TextField(blank=True)
    date_planifiee = models.DateTimeField()
    date_debut = models.DateTimeField(null=True, blank=True)
    date_fin = models.DateTimeField(null=True, blank=True)
    statut = models.CharField(
        max_length=20, choices=StatutMaintenance.choices, default=StatutMaintenance.PLANIFIEE
    )
    rapport = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Maintenance'
        verbose_name_plural = 'Maintenances'
        ordering = ['-date_planifiee']

    def __str__(self):
        return f'{self.equipement} — {self.get_type_display()} — {self.get_statut_display()}'

    def clean(self):
        if self.date_debut and self.date_fin and self.date_fin <= self.date_debut:
            raise ValidationError("La date de fin doit être postérieure à la date de début.")

    # --- Création ---

    def planifier(self):
        """
        Planifie une maintenance préventive et bascule l'équipement en
        EN_MAINTENANCE, pour qu'il ne soit plus réservable entre-temps.
        """
        self.full_clean()
        self.statut = StatutMaintenance.PLANIFIEE
        self.save()
        self.equipement.changer_statut(StatutEquipement.EN_MAINTENANCE)
        return self
    

    @classmethod
    def creer_depuis_signalement(cls, equipement, description, signale_par=None):
        """
        Traduit le diagramme de séquence 'Signalement d'une panne' :
        crée automatiquement une intervention corrective et bascule
        l'équipement en EN_PANNE.
        """
        equipement.changer_statut(StatutEquipement.EN_PANNE)
        maintenance = cls(
            equipement=equipement,
            type=TypeMaintenance.CORRECTIVE,
            description=description,
            date_planifiee=timezone.now(),
            signale_par=signale_par,
        )
        maintenance.full_clean()
        maintenance.save()
        return maintenance

    # --- Cycle de vie ---

    def demarrer(self):
        if self.statut != StatutMaintenance.PLANIFIEE:
            raise ValidationError("Seule une maintenance planifiée peut être démarrée.")
        self.statut = StatutMaintenance.EN_COURS
        self.date_debut = timezone.now()
        self.save(update_fields=['statut', 'date_debut'])

    def cloturer(self, rapport):
        if self.statut not in [StatutMaintenance.PLANIFIEE, StatutMaintenance.EN_COURS]:
            raise ValidationError("Cette maintenance ne peut plus être clôturée.")
        self.statut = StatutMaintenance.TERMINEE
        self.rapport = rapport
        self.date_fin = timezone.now()
        self.save(update_fields=['statut', 'rapport', 'date_fin'])
        self.equipement.changer_statut(StatutEquipement.DISPONIBLE)
        # TODO : notifier l'utilisateur qui avait signalé la panne, si applicable.

    def annuler(self):
        self.statut = StatutMaintenance.ANNULEE
        self.save(update_fields=['statut'])
        # On ne libère l'équipement que s'il n'a pas d'autre intervention active.
        maintenances_actives = self.equipement.maintenances.filter(
            statut__in=[StatutMaintenance.PLANIFIEE, StatutMaintenance.EN_COURS]
        ).exclude(pk=self.pk)
        if not maintenances_actives.exists():
            self.equipement.changer_statut(StatutEquipement.DISPONIBLE)