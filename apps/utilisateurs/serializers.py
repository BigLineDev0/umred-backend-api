from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from django.contrib.auth.models import update_last_login
from apps.core.services import enregistrer as journaliser
from .models import StatutCompte

from rest_framework import serializers
from .models import Utilisateur, Role


class UmredTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role  # accessible par FastAPI sans rappeler Django
        token['prenom'] = user.prenom
        token['nom'] = user.nom
        return token
    
    def validate(self, attrs):
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


class UtilisateurCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['nom', 'prenom', 'email', 'telephone', 'role', 'statut_academique']

    def validate_email(self, value):
        if Utilisateur.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Un compte existe déjà avec cette adresse email.')
        return value
    
class DefinirMotDePasseSerializer(serializers.Serializer):
    jeton = serializers.CharField()
    password = serializers.CharField(min_length=8, write_only=True)
    
    
class MonProfilUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['nom', 'prenom', 'telephone', 'photo']

    def validate_photo(self, value):
        if value and value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("L'image ne doit pas dépasser 5 Mo.")
        return value