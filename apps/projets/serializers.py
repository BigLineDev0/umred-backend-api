from rest_framework import serializers
from .models import Projet


class ProjetSerializer(serializers.ModelSerializer):
    responsable_nom = serializers.CharField(source='responsable.nom_complet', read_only=True)

    class Meta:
        model = Projet
        fields = ['id', 'nom', 'description', 'responsable', 'responsable_nom', 'niveau_priorite', 'date_creation']
        read_only_fields = ['responsable', 'date_creation']


class ProjetCreateSerializer(serializers.ModelSerializer):
    """Un chercheur crée son projet sans pouvoir s'auto-attribuer une priorité élevée."""
    class Meta:
        model = Projet
        fields = ['nom', 'description']