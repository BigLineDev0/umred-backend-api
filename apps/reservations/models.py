from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from apps.laboratoires.models import Laboratoire
from apps.equipements.models import Equipement
from django.utils import timezone


class StatutReservation(models.TextChoices):
    EN_ATTENTE = 'EN_ATTENTE', 'En attente'
    VALIDEE = 'VALIDEE', 'Validée'
    REFUSEE = 'REFUSEE', 'Refusée'
    ANNULEE = 'ANNULEE', 'Annulée'
    TERMINEE = 'TERMINEE', 'Terminée'


class Reservation(models.Model):
    demandeur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reservations_effectuees'
    )
    validateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reservations_validees'
    )
    laboratoire = models.ForeignKey(
        Laboratoire, on_delete=models.CASCADE, related_name='reservations'
    )
    equipements = models.ManyToManyField(
        Equipement, blank=True, related_name='reservations'
    )

    date = models.DateField()
    heure_debut = models.TimeField()
    heure_fin = models.TimeField()
    motif = models.TextField()
    statut = models.CharField(
        max_length=20, choices=StatutReservation.choices, default=StatutReservation.EN_ATTENTE
    )
    est_archivee = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_validation = models.DateTimeField(null=True, blank=True)
    rappel_envoye = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Réservation'
        verbose_name_plural = 'Réservations'
        ordering = ['-date', '-heure_debut']

    def __str__(self):
        return f'{self.demandeur} — {self.laboratoire} — {self.date}'

    def clean(self):
        if self.heure_fin <= self.heure_debut:
            raise ValidationError("L'heure de fin doit être après l'heure de début.")

    @staticmethod
    def _verifier_equipements(laboratoire, equipements_ids):
        if not equipements_ids:
            return
        trouves = Equipement.objects.filter(
            id__in=equipements_ids, laboratoire=laboratoire
        ).count()
        if trouves != len(set(equipements_ids)):
            raise ValidationError(
                "Un ou plusieurs équipements sélectionnés n'appartiennent pas à ce laboratoire."
            )

    @classmethod
    def _a_un_conflit(cls, date, heure_debut, heure_fin, equipements_ids, exclure_pk=None):
        """
        Vérifie le chevauchement pour CHAQUE équipement sélectionné :
        s'il en existe un seul déjà pris sur ce créneau, la réservation
        entière est refusée (pas de réservation partielle possible).
        """
        if not equipements_ids:
            return False
        qs = cls.objects.filter(
            equipements__id__in=equipements_ids,
            date=date,
            statut__in=[StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE],
            heure_debut__lt=heure_fin,
            heure_fin__gt=heure_debut,
        ).distinct()
        if exclure_pk:
            qs = qs.exclude(pk=exclure_pk)
        return qs.exists()

    def creer(self, equipements_ids=None):
        """
        Applique RG2/RG3, puis assigne les équipements APRÈS la sauvegarde :
        un ManyToMany ne peut pas être défini sur un objet qui n'a pas
        encore de pk en base — contrainte propre aux relations M2M.
        """
        from apps.utilisateurs.models import Role

        equipements_ids = equipements_ids or []

        self.full_clean(exclude=['equipements'])
        self._verifier_equipements(self.laboratoire, equipements_ids)

        if self._a_un_conflit(self.date, self.heure_debut, self.heure_fin, equipements_ids):
            raise ValidationError(
                "Un des équipements sélectionnés est déjà réservé ou en attente sur ce créneau."
            )

        self.statut = (
            StatutReservation.EN_ATTENTE if self.demandeur.role == Role.ETUDIANT
            else StatutReservation.VALIDEE
        )
        self.save()
        if equipements_ids:
            self.equipements.set(equipements_ids)
        return self

    def valider(self, validateur):
        self.statut = StatutReservation.VALIDEE
        self.validateur = validateur
        self.date_validation = timezone.now()
        self.save(update_fields=['statut', 'validateur', 'date_validation'])

    def refuser(self, validateur):
        self.statut = StatutReservation.REFUSEE
        self.validateur = validateur
        self.date_validation = timezone.now()
        self.save(update_fields=['statut', 'validateur', 'date_validation'])

    def annuler(self):
        self.statut = StatutReservation.ANNULEE
        self.save(update_fields=['statut'])

    def archiver(self):
        self.est_archivee = True
        self.save(update_fields=['est_archivee'])

    def desarchiver(self):
        self.est_archivee = False
        self.save(update_fields=['est_archivee'])