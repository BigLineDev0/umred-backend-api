from rest_framework import serializers
from .models import Consommable, MouvementStock


class ConsommableSerializer(serializers.ModelSerializer):
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)
    statut = serializers.CharField(read_only=True)
    peremption_proche = serializers.BooleanField(read_only=True)

    class Meta:
        model = Consommable
        fields = [
            'id', 'laboratoire', 'laboratoire_nom', 'nom', 'reference', 'unite',
            'quantite_stock', 'seuil_alerte', 'date_peremption', 'statut',
            'peremption_proche', 'date_creation',
        ]
        read_only_fields = ['date_creation']


class MouvementStockSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.CharField(source='utilisateur.nom_complet', read_only=True, allow_null=True)
    consommable_nom = serializers.CharField(source='consommable.nom', read_only=True)

    class Meta:
        model = MouvementStock
        fields = ['id', 'consommable', 'consommable_nom', 'type', 'quantite', 'utilisateur', 'utilisateur_nom', 'motif', 'date_mouvement']
        read_only_fields = ['utilisateur', 'date_mouvement']


class RetirerStockSerializer(serializers.Serializer):
    quantite = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    motif = serializers.CharField(required=False, allow_blank=True)


class ReapprovisionnerSerializer(serializers.Serializer):
    quantite = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    motif = serializers.CharField(required=False, allow_blank=True)