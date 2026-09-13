from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from apps.laboratoires.models import Laboratoire
from apps.equipements.models import Equipement


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
    equipement = models.ForeignKey(
        Equipement, on_delete=models.CASCADE, related_name='reservations', null=True, blank=True
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

    class Meta:
        verbose_name = 'Réservation'
        verbose_name_plural = 'Réservations'
        ordering = ['-date', '-heure_debut']

    def __str__(self):
        return f'{self.demandeur} — {self.laboratoire} — {self.date}'

    # --- Validation métier ---

    def clean(self):
        if self.heure_fin <= self.heure_debut:
            raise ValidationError("L'heure de fin doit être après l'heure de début.")

        if self.equipement and self.equipement.laboratoire_id != self.laboratoire_id:
            raise ValidationError("L'équipement sélectionné n'appartient pas à ce laboratoire.")

        if self.a_un_conflit():
            raise ValidationError("Ce créneau est déjà réservé ou en attente sur cet équipement.")

    # --- Détection de chevauchement (uniquement sur l'équipement) ---

    def a_un_conflit(self):
        """
        Vérifie s'il existe déjà une réservation EN_ATTENTE ou VALIDEE
        sur le même équipement qui chevauche cette plage horaire.
        Retourne False si aucun équipement n'est réservé (pas de conflit
        possible sur le laboratoire seul, il n'est pas exclusif).
        """
        if not self.equipement:
            return False

        conflits = Reservation.objects.filter(
            equipement=self.equipement,
            date=self.date,
            statut__in=[StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE],
            heure_debut__lt=self.heure_fin,
            heure_fin__gt=self.heure_debut,
        ).exclude(pk=self.pk)

        return conflits.exists()

    # --- Cycle de vie ---

    def creer(self):
        """
        Applique la règle RG2/RG3 : confirmation immédiate pour un membre
        du laboratoire, mise en attente pour un étudiant.
        """
        from apps.utilisateurs.models import Role

        if self.demandeur.role == Role.ETUDIANT:
            self.statut = StatutReservation.EN_ATTENTE
        else:
            self.statut = StatutReservation.VALIDEE

        self.full_clean()   # déclenche clean(), qui vérifie maintenant le conflit
        self.save()
        return self

    def valider(self, validateur):
        self.statut = StatutReservation.VALIDEE
        self.validateur = validateur
        from django.utils import timezone
        self.date_validation = timezone.now()
        self.save(update_fields=['statut', 'validateur', 'date_validation'])

    def refuser(self, validateur):
        self.statut = StatutReservation.REFUSEE
        self.validateur = validateur
        from django.utils import timezone
        self.date_validation = timezone.now()
        self.save(update_fields=['statut', 'validateur', 'date_validation'])

    def annuler(self):
        self.statut = StatutReservation.ANNULEE
        self.save(update_fields=['statut'])

    def modifier(self, **champs):
        for champ, valeur in champs.items():
            setattr(self, champ, valeur)
        self.full_clean()
        self.save()
        
    def archiver(self):
        self.est_archivee = True
        self.save(update_fields=['est_archivee'])

    def desarchiver(self):
        self.est_archivee = False
        self.save(update_fields=['est_archivee'])