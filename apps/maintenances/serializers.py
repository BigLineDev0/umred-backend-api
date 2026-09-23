from rest_framework import serializers
from apps.equipements.models import Equipement
from .models import Maintenance


class MaintenanceSerializer(serializers.ModelSerializer):
    equipement_nom = serializers.CharField(source='equipement.nom', read_only=True)
    equipement_numero_serie = serializers.CharField(source='equipement.numero_serie', read_only=True)
    equipement_laboratoire_nom = serializers.CharField(source='equipement.laboratoire.nom', read_only=True)
    equipement_statut = serializers.CharField(source='equipement.statut', read_only=True)
    technicien_nom = serializers.CharField(source='technicien.nom_complet', read_only=True, allow_null=True)
    signale_par_nom = serializers.CharField(source='signale_par.nom_complet', read_only=True, allow_null=True)

    class Meta:
        model = Maintenance
        fields = [
            'id', 'equipement', 'equipement_nom', 'equipement_numero_serie',
            'equipement_laboratoire_nom', 'equipement_statut',
            'technicien', 'technicien_nom', 'signale_par', 'signale_par_nom',
            'type', 'description', 'date_planifiee', 'date_debut', 'date_fin',
            'statut', 'rapport', 'date_creation',
        ]
        read_only_fields = ['technicien', 'statut', 'date_debut', 'date_fin', 'date_creation', 'signale_par']


class SignalementPanneSerializer(serializers.Serializer):
    equipement = serializers.PrimaryKeyRelatedField(queryset=Equipement.objects.all())
    description = serializers.CharField()


class ClotureMaintenanceSerializer(serializers.Serializer):
    rapport = serializers.CharField()