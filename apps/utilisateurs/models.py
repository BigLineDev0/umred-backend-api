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


# Rang numérique utilisé pour départager deux demandes en conflit à
# priorité de projet égale (voir ReservationViewSet._cle_priorite).
# Plus le chiffre est grand, plus le demandeur est prioritaire.
RANG_STATUT_ACADEMIQUE = {
    StatutAcademique.PROFESSEUR: 3,
    StatutAcademique.MAITRE_DE_CONFERENCES: 2,
    StatutAcademique.DOCTORANT: 1,
}
class UtilisateurManager(BaseUserManager):

    # Les emails sont stockés en minuscules et recherchés sans tenir compte
    # de la casse : « Awa@umred.sn » et « awa@umred.sn » désignent le même
    # compte (impossible d'en créer deux, connexion possible dans les deux cas).
    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("L'adresse email est obligatoire.")
        email = self.normalize_email(email).lower()
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
    
    # statut_compte est le statut métier affiché ; is_active est le drapeau
    # que Django et simplejwt vérifient à CHAQUE requête authentifiée. Les
    # deux sont synchronisés : désactiver un compte coupe immédiatement
    # l'accès, même avec un token JWT encore valide.
    def activer_compte(self):
        self.statut_compte = StatutCompte.ACTIF
        self.is_active = True
        self.save(update_fields=['statut_compte', 'is_active'])

    def desactiver_compte(self):
        self.statut_compte = StatutCompte.INACTIF
        self.is_active = False
        self.save(update_fields=['statut_compte', 'is_active'])


class MotifJeton(models.TextChoices):
    INVITATION = 'INVITATION', 'Activation du compte'
    REINITIALISATION = 'REINITIALISATION', 'Mot de passe oublié'


class JetonDefinitionMotDePasse(models.Model):
    """
    Jeton envoyé par email pour choisir un mot de passe, dans deux cas :
    l'invitation d'un compte créé par l'admin, et le « mot de passe
    oublié ». Usage unique. secrets.token_urlsafe(32) produit 32 octets
    aléatoires cryptographiquement sûrs (~43 caractères), impossibles à
    deviner, et utilisables tels quels dans une URL.
    """
    # Durée de vie selon le motif : une invitation laisse le temps de
    # consulter ses emails ; un lien de réinitialisation, qui donne accès à
    # un compte existant, doit être court pour limiter le risque en cas de
    # boîte mail compromise.
    DUREES_VALIDITE = {
        MotifJeton.INVITATION: timedelta(days=3),
        MotifJeton.REINITIALISATION: timedelta(hours=1),
    }

    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='jetons_mdp')
    jeton = models.CharField(max_length=64, unique=True, db_index=True)
    motif = models.CharField(max_length=20, choices=MotifJeton.choices, default=MotifJeton.INVITATION)
    date_creation = models.DateTimeField(auto_now_add=True)
    utilise = models.BooleanField(default=False)

    def est_valide(self):
        expiration = self.date_creation + self.DUREES_VALIDITE[self.motif]
        return not self.utilise and timezone.now() < expiration

    @classmethod
    def generer_pour(cls, utilisateur, motif=MotifJeton.INVITATION):
        # Un seul lien actif à la fois : générer un nouveau jeton invalide
        # les précédents (ex. si l'utilisateur clique deux fois sur
        # « mot de passe oublié », seul le dernier email fonctionne).
        cls.objects.filter(utilisateur=utilisateur, utilise=False).update(utilise=True)
        return cls.objects.create(utilisateur=utilisateur, jeton=secrets.token_urlsafe(32), motif=motif)