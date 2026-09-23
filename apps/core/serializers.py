from rest_framework import serializers
from .models import JournalActivite


class JournalActiviteSerializer(serializers.ModelSerializer):
    auteur_nom = serializers.CharField(source='auteur.nom_complet', read_only=True, allow_null=True)
    entite_type_nom = serializers.CharField(source='entite_type.model', read_only=True, allow_null=True)

    class Meta:
        model = JournalActivite
        fields = ['id', 'auteur', 'auteur_nom', 'action', 'description',
                  'entite_type_nom', 'entite_id', 'date_heure']