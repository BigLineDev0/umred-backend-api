from django.conf import settings
from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
from apps.equipements.models import Equipement, StatutEquipement


class TypeMaintenance(models.TextChoices):
    PREVENTIVE = 'PREVENTIVE', 'Préventive'
    CORRECTIVE = 'CORRECTIVE', 'Corrective'


class StatutMaintenance(models.TextChoices):
    SIGNALEE = 'SIGNALEE', 'Signalée'
    PLANIFIEE = 'PLANIFIEE', 'Planifiée'
    EN_COURS = 'EN_COURS', 'En cours'
    TERMINEE = 'TERMINEE', 'Terminée'
    ANNULEE = 'ANNULEE', 'Annulée'


# Une maintenance dans l'un de ces états immobilise encore l'équipement.
STATUTS_ACTIFS = [StatutMaintenance.SIGNALEE, StatutMaintenance.PLANIFIEE, StatutMaintenance.EN_COURS]


class Maintenance(models.Model):
    """
    Cycle de vie (machine à états) :

      Panne signalée :   SIGNALEE --prendre_en_charge--> PLANIFIEE
      Préventive :       (création) ------------------> PLANIFIEE
      PLANIFIEE --demarrer--> EN_COURS --cloturer--> TERMINEE
      (SIGNALEE peut aussi être démarrée directement si un technicien est assigné)
      Tout état actif --annuler--> ANNULEE

    Le statut de l'équipement suit la maintenance : EN_PANNE dès le
    signalement d'une panne, EN_MAINTENANCE pendant l'intervention
    (EN_COURS), DISPONIBLE à la clôture. Une préventive simplement
    PLANIFIÉE n'immobilise pas l'équipement : seule sa journée
    d'intervention est fermée aux réservations (voir jour_bloque).
    Les réservations déjà prises ce jour-là sont prévenues à la
    planification. Chaque transition vérifie l'état de départ et lève une
    ValidationError si elle n'est pas autorisée.

    Un équipement peut avoir plusieurs maintenances actives en même temps
    (ex. une panne signalée pendant qu'une préventive est planifiée) : son
    statut est donc toujours RECALCULÉ à partir de l'ensemble de ses
    maintenances actives (voir _recalculer_statut_equipement), jamais
    simplement remis à DISPONIBLE à la fin de l'une d'elles.
    Chaque opération est atomique : la maintenance et le statut de
    l'équipement sont enregistrés ensemble, ou pas du tout.
    """
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

    def _recalculer_statut_equipement(self):
        """
        Priorité : une panne (maintenance corrective active) l'emporte sur
        une intervention en cours ; sans panne ni intervention en cours,
        l'équipement est disponible (une préventive planifiée plus tard ne
        l'immobilise pas dès aujourd'hui). Un équipement HORS_SERVICE (retiré du parc
        par décision humaine) n'est jamais modifié automatiquement.
        """
        equipement = self.equipement
        if equipement.statut == StatutEquipement.HORS_SERVICE:
            return
        actives = equipement.maintenances.filter(statut__in=STATUTS_ACTIFS)
        if actives.filter(type=TypeMaintenance.CORRECTIVE).exists():
            nouveau = StatutEquipement.EN_PANNE
        elif actives.filter(statut=StatutMaintenance.EN_COURS).exists():
            nouveau = StatutEquipement.EN_MAINTENANCE
        else:
            nouveau = StatutEquipement.DISPONIBLE
        if equipement.statut != nouveau:
            equipement.changer_statut(nouveau)

    # --- Création ---

    @classmethod
    def jour_bloque(cls, equipements_ids, jour):
        """Une maintenance planifiée ou en cours ferme la journée aux réservations."""
        return cls.objects.filter(
            equipement_id__in=equipements_ids, date_planifiee__date=jour,
            statut__in=[StatutMaintenance.PLANIFIEE, StatutMaintenance.EN_COURS],
        ).select_related('equipement')

    def planifier(self):
        """
        Planifie une maintenance. L'équipement reste réservable jusqu'au
        jour de l'intervention, qui est fermé aux réservations.
        """
        if self.date_planifiee < timezone.now() - timedelta(minutes=5):
            raise ValidationError("La date planifiée est déjà passée.")
        self.full_clean()
        with transaction.atomic():
            self.statut = StatutMaintenance.PLANIFIEE
            self.save()
            self._recalculer_statut_equipement()
        return self


    @classmethod
    def creer_depuis_signalement(cls, equipement, description, signale_par=None):
        """
        Traduit le diagramme de séquence 'Signalement d'une panne' :
        crée automatiquement une intervention corrective et bascule
        l'équipement en EN_PANNE.
        """
        if equipement.statut == StatutEquipement.HORS_SERVICE:
            raise ValidationError("Cet équipement est hors service : aucune panne ne peut y être signalée.")
        maintenance = cls(
            equipement=equipement,
            type=TypeMaintenance.CORRECTIVE,
            description=description,
            date_planifiee=timezone.now(),
            signale_par=signale_par,
            statut=StatutMaintenance.SIGNALEE,  # jamais PLANIFIEE tant que personne n'a agi
        )
        # Validation AVANT toute écriture, puis maintenance + statut de
        # l'équipement enregistrés dans la même transaction.
        maintenance.full_clean()
        with transaction.atomic():
            maintenance.save()
            maintenance._recalculer_statut_equipement()
        return maintenance

    # --- Cycle de vie ---

    def demarrer(self):
        if self.statut not in [StatutMaintenance.PLANIFIEE, StatutMaintenance.SIGNALEE]:
            raise ValidationError("Seule une maintenance planifiée ou signalée peut être démarrée.")
        if self.statut == StatutMaintenance.SIGNALEE and not self.technicien:
            raise ValidationError("Assignez d'abord un technicien avant de démarrer.")
        with transaction.atomic():
            self.statut = StatutMaintenance.EN_COURS
            self.date_debut = timezone.now()
            self.save(update_fields=['statut', 'date_debut'])
            # Corrigé : l'équipement n'était jamais basculé EN_MAINTENANCE au démarrage.
            self._recalculer_statut_equipement()

    def cloturer(self, rapport):
        if self.statut not in [StatutMaintenance.PLANIFIEE, StatutMaintenance.EN_COURS]:
            raise ValidationError("Cette maintenance ne peut plus être clôturée.")
        with transaction.atomic():
            self.statut = StatutMaintenance.TERMINEE
            self.rapport = rapport
            self.date_fin = timezone.now()
            self.save(update_fields=['statut', 'rapport', 'date_fin'])
            self._recalculer_statut_equipement()
        # La notification de celui qui a signalé la panne est faite dans la
        # vue (MaintenanceViewSet.cloturer). date_fin sert aussi de point de
        # départ au compteur d'heures d'usure de l'équipement.

    def annuler(self):
        # Une maintenance terminée ou déjà annulée fait partie de
        # l'historique de l'équipement : on ne la réécrit pas.
        if self.statut in [StatutMaintenance.TERMINEE, StatutMaintenance.ANNULEE]:
            raise ValidationError("Cette maintenance est déjà terminée ou annulée.")
        with transaction.atomic():
            self.statut = StatutMaintenance.ANNULEE
            self.save(update_fields=['statut'])
            self._recalculer_statut_equipement()

    def prendre_en_charge(self, technicien, date_planifiee):
        if self.statut != StatutMaintenance.SIGNALEE:
            raise ValidationError("Seule une panne signalée peut être prise en charge.")
        if date_planifiee < timezone.now() - timedelta(minutes=5):
            raise ValidationError("La date d'intervention est déjà passée.")
        self.technicien = technicien
        self.date_planifiee = date_planifiee
        self.statut = StatutMaintenance.PLANIFIEE
        self.save(update_fields=['technicien', 'date_planifiee', 'statut'])
        return self