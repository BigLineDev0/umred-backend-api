from rest_framework import serializers
from .models import Equipement


class EquipementSerializer(serializers.ModelSerializer):
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)

    class Meta:
        model = Equipement
        fields = [
            'id', 'laboratoire', 'laboratoire_nom', 'nom', 'description',
            'marque', 'modele', 'numero_serie', 'statut', 'date_creation',
        ]
        read_only_fields = ['date_creation']