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
    instructions_utilisation = models.TextField(blank=True)
    consignes_securite = models.TextField(blank=True)
    manuel_pdf = models.FileField(upload_to='manuels_equipements/', blank=True, null=True)
    necessite_validation = models.BooleanField(
        default=False,
        help_text="Équipement coûteux ou sensible : toute réservation nécessite une validation, quel que soit le rôle du demandeur."
    )
    seuil_heures_maintenance = models.PositiveIntegerField(
        default=200,
        help_text="Nombre d'heures d'utilisation cumulées avant qu'une alerte de maintenance préventive soit déclenchée."
    )
    categorie = models.CharField(
        max_length=100, blank=True,
        help_text="Ex. 'Microscope', 'Centrifugeuse' — permet de suggérer un équipement équivalent en cas de conflit."
    )

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
        
    def heures_utilisation_depuis_derniere_maintenance(self):
        """
        Additionne la durée de toutes les réservations VALIDEES ou TERMINEES
        sur cet équipement, depuis la dernière maintenance clôturée (ou
        depuis toujours, si aucune maintenance n'a jamais eu lieu).
        """
        from apps.maintenances.models import StatutMaintenance
        from apps.reservations.models import StatutReservation
        from datetime import datetime, timedelta

        derniere_maintenance = self.maintenances.filter(
            statut=StatutMaintenance.TERMINEE
        ).order_by('-date_fin').first()

        reservations = self.reservations.filter(
            statut__in=[StatutReservation.VALIDEE, StatutReservation.TERMINEE]
        )
        if derniere_maintenance and derniere_maintenance.date_fin:
            reservations = reservations.filter(date__gte=derniere_maintenance.date_fin.date())

        total = timedelta()
        for r in reservations:
            debut = datetime.combine(r.date, r.heure_debut)
            fin = datetime.combine(r.date, r.heure_fin)
            total += (fin - debut)

        return round(total.total_seconds() / 3600, 1)
    
    def pannes_signalees_recentes(self, jours=90):
        """
        Le calcul de fréquence de pannes récentes
        """
        from apps.maintenances.models import TypeMaintenance
        from django.utils import timezone
        from datetime import timedelta

        seuil_date = timezone.now() - timedelta(days=jours)
        return self.maintenances.filter(
            type=TypeMaintenance.CORRECTIVE,
            date_creation__gte=seuil_date,
        ).count()