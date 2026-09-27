from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from django.contrib.auth.models import update_last_login
from apps.core.services import enregistrer as journaliser
from .models import StatutCompte

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from .models import Utilisateur, Role


def verifier_robustesse(password, utilisateur):
    """
    Applique les AUTH_PASSWORD_VALIDATORS de settings.py : longueur
    minimale, mot de passe trop courant, entièrement numérique, ou trop
    proche du nom/prénom/email de l'utilisateur (d'où le paramètre
    utilisateur). Convertit l'erreur Django en erreur DRF (réponse 400).
    """
    try:
        validate_password(password, utilisateur)
    except DjangoValidationError as e:
        raise serializers.ValidationError({'password': e.messages})


def verifier_email_unique(email, instance=None):
    # Comparaison insensible à la casse, et stockage en minuscules.
    email = email.lower()
    qs = Utilisateur.objects.filter(email__iexact=email)
    if instance is not None:
        qs = qs.exclude(pk=instance.pk)
    if qs.exists():
        raise serializers.ValidationError('Un compte existe déjà avec cette adresse email.')
    return email


class UmredTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role  # accessible par FastAPI sans rappeler Django
        token['prenom'] = user.prenom
        token['nom'] = user.nom
        return token
    
    def validate(self, attrs):
        # super().validate() vérifie email + mot de passe et génère les
        # tokens ; un compte désactivé (is_active=False) y est déjà refusé.
        # On ajoute ensuite une règle métier : un compte non ACTIF (ex. EN_ATTENTE)
        # ne peut pas se connecter, même avec le bon mot de passe.
        data = super().validate(attrs)

        if self.user.statut_compte != StatutCompte.ACTIF:
            raise AuthenticationFailed(
                "Ce compte n'est pas encore actif. Contactez un administrateur.",
                code='compte_inactif'
            )

        update_last_login(None, self.user)
        journaliser(self.user, 'Connexion à la plateforme', self.user)

        data['role'] = self.user.role
        data['nom'] = self.user.nom
        data['prenom'] = self.user.prenom
        data['photo'] = self.user.photo.url if self.user.photo else None
        return data
    

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Utilisateur
        fields = ['nom', 'prenom', 'email', 'password']

    def validate_email(self, value):
        return verifier_email_unique(value)

    def validate(self, attrs):
        # Utilisateur temporaire (non sauvegardé) pour que le validateur de
        # similarité puisse comparer le mot de passe au nom/prénom/email.
        verifier_robustesse(attrs['password'], Utilisateur(
            email=attrs['email'], nom=attrs['nom'], prenom=attrs['prenom'],
        ))
        return attrs

    def create(self, validated_data):
        return Utilisateur.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            nom=validated_data['nom'],
            prenom=validated_data['prenom'],
            role=Role.ETUDIANT,   # jamais fourni par le client, toujours forcé ici
        )
        
class UtilisateurSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['id', 'nom', 'prenom', 'email', 'telephone', 'role', 'photo', 'statut_compte', 'statut_academique', 'date_creation', 'last_login']
        read_only_fields = ['statut_compte', 'date_creation', 'last_login']

    def validate_email(self, value):
        return verifier_email_unique(value, self.instance)


class UtilisateurCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['nom', 'prenom', 'email', 'telephone', 'role', 'statut_academique']

    def validate_email(self, value):
        return verifier_email_unique(value)


class DefinirMotDePasseSerializer(serializers.Serializer):
    jeton = serializers.CharField()
    password = serializers.CharField(min_length=8, write_only=True)


class ChangerMotDePasseSerializer(serializers.Serializer):
    ancien_password = serializers.CharField(write_only=True)
    nouveau_password = serializers.CharField(write_only=True)

    def validate_ancien_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Le mot de passe actuel est incorrect.')
        return value

    def validate(self, attrs):
        try:
            validate_password(attrs['nouveau_password'], self.context['request'].user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({'nouveau_password': e.messages})
        return attrs


class MotDePasseOublieSerializer(serializers.Serializer):
    email = serializers.EmailField()
    
    
class MonProfilUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['nom', 'prenom', 'telephone', 'photo']

    def validate_photo(self, value):
        if value and value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("L'image ne doit pas dépasser 5 Mo.")
        return value