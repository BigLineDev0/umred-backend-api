from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models

import secrets
from datetime import timedelta
from django.utils import timezone


class Role(models.TextChoices):
    ADMIN = 'ADMIN', 'Administrateur'
    TECHNICIEN = 'TECHNICIEN', 'Technicien'
    CHERCHEUR = 'CHERCHEUR', 'Membre du laboratoire'
    ETUDIANT = 'ETUDIANT', 'Étudiant'


class StatutCompte(models.TextChoices):
    EN_ATTENTE = 'EN_ATTENTE', 'En attente'
    ACTIF = 'ACTIF', 'Actif'
    INACTIF = 'INACTIF', 'Inactif'

class StatutAcademique(models.TextChoices):
    DOCTORANT = 'DOCTORANT', 'Doctorant'
    MAITRE_DE_CONFERENCES = 'MAITRE_DE_CONFERENCES', 'Maître de conférences'
    PROFESSEUR = 'PROFESSEUR', 'Professeur des universités'


RANG_STATUT_ACADEMIQUE = {
    StatutAcademique.PROFESSEUR: 3,
    StatutAcademique.MAITRE_DE_CONFERENCES: 2,
    StatutAcademique.DOCTORANT: 1,
}
class UtilisateurManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("L'adresse email est obligatoire.")
        email = self.normalize_email(email)
        extra_fields.setdefault('statut_compte', StatutCompte.ACTIF)
        utilisateur = self.model(email=email, **extra_fields)
        utilisateur.set_password(password)
        utilisateur.save(using=self._db)
        return utilisateur

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('role', Role.ADMIN)
        extra_fields.setdefault('statut_compte', StatutCompte.ACTIF)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class Utilisateur(AbstractBaseUser, PermissionsMixin):
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    telephone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    photo = models.ImageField(upload_to='photos_profil/', blank=True, null=True)
    statut_compte = models.CharField(
        max_length=20, choices=StatutCompte.choices, default=StatutCompte.ACTIF
    )
    statut_academique = models.CharField(
        max_length=30, choices=StatutAcademique.choices, blank=True, null=True,
        help_text="Uniquement pertinent pour un enseignant-chercheur ; laissé vide pour les autres rôles."
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    objects = UtilisateurManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nom', 'prenom', 'role']

    class Meta:
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'
        ordering = ['nom', 'prenom']

    def __str__(self):
        return f'{self.prenom} {self.nom} ({self.get_role_display()})'

    @property
    def nom_complet(self):
        return f'{self.prenom} {self.nom}'
    
    def activer_compte(self):
        self.statut_compte = StatutCompte.ACTIF
        self.save(update_fields=['statut_compte'])

    def desactiver_compte(self):
        self.statut_compte = StatutCompte.INACTIF
        self.save(update_fields=['statut_compte'])


class JetonDefinitionMotDePasse(models.Model):
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='jetons_mdp')
    jeton = models.CharField(max_length=64, unique=True, db_index=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    utilise = models.BooleanField(default=False)

    def est_valide(self):
        expiration = self.date_creation + timedelta(days=3)
        return not self.utilise and timezone.now() < expiration

    @classmethod
    def generer_pour(cls, utilisateur):
        return cls.objects.create(utilisateur=utilisateur, jeton=secrets.token_urlsafe(32))