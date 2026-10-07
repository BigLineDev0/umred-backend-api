from django.utils import timezone

from rest_framework import serializers
from apps.core.validation import valider_texte_long
from apps.equipements.models import Equipement
from apps.organisations.isolation import ChampsOrganisationMixin
from .models import Maintenance


class MaintenanceSerializer(ChampsOrganisationMixin, serializers.ModelSerializer):
    champs_organisation = {'equipement': 'laboratoire__organisation'}
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

    def validate_description(self, value):
        return valider_texte_long(value, max_len=2000)

    def validate_date_planifiee(self, value):
        # Une maintenance se planifie dans le futur (contrôle à la création).
        if value and self.instance is None and value < timezone.now():
            raise serializers.ValidationError("La maintenance doit être planifiée dans le futur.")
        return value


class SignalementPanneSerializer(ChampsOrganisationMixin, serializers.Serializer):
    champs_organisation = {'equipement': 'laboratoire__organisation'}
    equipement = serializers.PrimaryKeyRelatedField(queryset=Equipement.objects.all())
    description = serializers.CharField()

    def validate_description(self, value):
        return valider_texte_long(value, min_len=5, max_len=2000, obligatoire=True)


class ClotureMaintenanceSerializer(serializers.Serializer):
    rapport = serializers.CharField()

    def validate_rapport(self, value):
        return valider_texte_long(value, min_len=5, max_len=2000, obligatoire=True)