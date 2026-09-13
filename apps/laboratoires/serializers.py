from rest_framework import serializers
from .models import Laboratoire


class LaboratoireSerializer(serializers.ModelSerializer):
    responsable_nom = serializers.CharField(source='responsable.__str__', read_only=True, allow_null=True)

    class Meta:
        model = Laboratoire
        fields = [
            'id', 'nom', 'description', 'localisation', 'statut',
            'photo', 'responsable', 'responsable_nom', 'date_creation',
        ]
        read_only_fields = ['date_creation']