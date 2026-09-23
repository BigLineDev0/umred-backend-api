from rest_framework import serializers
from .models import Laboratoire


class LaboratoireSerializer(serializers.ModelSerializer):
    responsable_nom = serializers.CharField(source='responsable.nom_complet', read_only=True, allow_null=True)
    nombre_equipements = serializers.SerializerMethodField()
    nombre_equipements_disponibles = serializers.SerializerMethodField()

    class Meta:
        model = Laboratoire
        fields = [
            'id', 'nom', 'description', 'localisation', 'capacite', 'statut',
            'photo', 'responsable', 'responsable_nom',
            'nombre_equipements', 'nombre_equipements_disponibles', 'date_creation',
        ]
        read_only_fields = ['date_creation']

    def get_nombre_equipements(self, obj):
        return obj.equipements.count()

    def get_nombre_equipements_disponibles(self, obj):
        return obj.equipements.filter(statut='DISPONIBLE').count()