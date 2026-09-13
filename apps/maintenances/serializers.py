from rest_framework import serializers
from apps.equipements.models import Equipement
from .models import Maintenance


class MaintenanceSerializer(serializers.ModelSerializer):
    equipement_nom = serializers.CharField(source='equipement.nom', read_only=True)
    technicien_nom = serializers.CharField(source='technicien.__str__', read_only=True, allow_null=True)

    class Meta:
        model = Maintenance
        fields = [
            'id', 'equipement', 'equipement_nom', 'technicien', 'technicien_nom',
            'type', 'description', 'date_planifiee', 'date_debut', 'date_fin',
            'statut', 'rapport', 'date_creation',
        ]
        read_only_fields = ['technicien', 'statut', 'date_debut', 'date_fin', 'date_creation']


class SignalementPanneSerializer(serializers.Serializer):
    equipement = serializers.PrimaryKeyRelatedField(queryset=Equipement.objects.all())
    description = serializers.CharField()


class ClotureMaintenanceSerializer(serializers.Serializer):
    rapport = serializers.CharField()