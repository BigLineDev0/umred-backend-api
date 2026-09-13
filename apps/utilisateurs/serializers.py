from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from .models import StatutCompte

from rest_framework import serializers
from .models import Utilisateur, Role


class UmredTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)

        if self.user.statut_compte != StatutCompte.ACTIF:
            raise AuthenticationFailed(
                "Ce compte n'est pas encore actif. Contactez un administrateur.",
                code='compte_inactif'
            )

        data['role'] = self.user.role
        data['nom'] = self.user.nom
        data['prenom'] = self.user.prenom
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