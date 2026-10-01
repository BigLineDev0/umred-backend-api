from datetime import time

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


couleur_hexa = RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Couleur attendue au format hexadécimal, ex. #1848D9.')


class Organisation(models.Model):
    """
    Un établissement client de la plateforme (université, UFR, institut).
    C'est l'unité d'isolation du SaaS : chaque laboratoire, et donc chaque
    équipement, réservation, maintenance ou consommable, appartient à une
    seule organisation, et un utilisateur ne voit que les données de la
    sienne. Seul le super-admin (éditeur de la solution) voit l'ensemble.

    L'organisation porte aussi sa configuration : identité visuelle et
    règles de réservation, que chaque établissement adapte à ses besoins
    sans toucher au code.
    """
    nom = models.CharField(max_length=150)
    slug = models.SlugField(max_length=60, unique=True, help_text="Identifiant court, utilisé dans les URL (ex. ucad-fst).")
    ville = models.CharField(max_length=100, blank=True)
    email_contact = models.EmailField(blank=True)

    # --- Identité visuelle ---
    logo = models.ImageField(upload_to='logos_organisations/', blank=True, null=True)
    couleur_primaire = models.CharField(max_length=7, default='#1848D9', validators=[couleur_hexa])
    couleur_secondaire = models.CharField(max_length=7, default='#0F172A', validators=[couleur_hexa])

    # --- Règles de réservation ---
    heure_ouverture = models.TimeField(default=time(8, 0))
    heure_fermeture = models.TimeField(default=time(19, 0))
    duree_min_reservation = models.PositiveIntegerField(
        default=30, help_text="Durée minimale d'une réservation, en minutes."
    )
    duree_max_reservation = models.PositiveIntegerField(
        default=480, help_text="Durée maximale d'une réservation, en minutes."
    )
    delai_max_reservation_jours = models.PositiveIntegerField(
        default=60, help_text="On ne peut pas réserver plus de N jours à l'avance."
    )

    est_active = models.BooleanField(default=True, help_text="Une organisation suspendue ne peut plus se connecter.")
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Organisation'
        verbose_name_plural = 'Organisations'
        ordering = ['nom']

    def __str__(self):
        return self.nom

    def clean(self):
        if self.heure_fermeture <= self.heure_ouverture:
            raise ValidationError("L'heure de fermeture doit être après l'heure d'ouverture.")
        if self.duree_min_reservation < 5:
            raise ValidationError("La durée minimale d'une réservation est d'au moins 5 minutes.")
        if self.duree_max_reservation < self.duree_min_reservation:
            raise ValidationError("La durée maximale doit être supérieure ou égale à la durée minimale.")
