from rest_framework import serializers
from .models import Equipement


class EquipementSerializer(serializers.ModelSerializer):
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)

    class Meta:
        model = Equipement
        fields = [
            'id', 'laboratoire', 'laboratoire_nom', 'nom', 'description',
            'marque', 'modele', 'numero_serie', 'statut', 'date_creation',
            'instructions_utilisation', 'consignes_securite', 'manuel_pdf',
            'necessite_validation'
        ]
        read_only_fields = ['date_creation']
        
    def validate_manuel_pdf(self, value):
        if value and value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("Le fichier ne doit pas dépasser 10 Mo.")
        if value and not value.name.lower().endswith('.pdf'):
            raise serializers.ValidationError("Seuls les fichiers PDF sont acceptés.")
        return value