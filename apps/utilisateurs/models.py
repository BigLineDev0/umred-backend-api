from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models


class Role(models.TextChoices):
    ADMIN = 'ADMIN', 'Administrateur'
    TECHNICIEN = 'TECHNICIEN', 'Technicien'
    CHERCHEUR = 'CHERCHEUR', 'Membre du laboratoire'
    ETUDIANT = 'ETUDIANT', 'Étudiant'


class StatutCompte(models.TextChoices):
    EN_ATTENTE = 'EN_ATTENTE', 'En attente'
    ACTIF = 'ACTIF', 'Actif'
    INACTIF = 'INACTIF', 'Inactif'


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
    photo = models.URLField(blank=True, null=True)
    statut_compte = models.CharField(
        max_length=20, choices=StatutCompte.choices, default=StatutCompte.ACTIF
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
        return f'{self.prenom} {self.nom}'

    def activer_compte(self):
        self.statut_compte = StatutCompte.ACTIF
        self.save(update_fields=['statut_compte'])

    def desactiver_compte(self):
        self.statut_compte = StatutCompte.INACTIF
        self.save(update_fields=['statut_compte'])