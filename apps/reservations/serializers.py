from rest_framework import serializers
from .models import Reservation

class ReservationSerializer(serializers.ModelSerializer):
    demandeur_nom = serializers.CharField(source='demandeur.__str__', read_only=True)
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)
    equipements_noms = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            'id', 'demandeur', 'demandeur_nom', 'validateur',
            'laboratoire', 'laboratoire_nom', 'equipements', 'equipements_noms',
            'date', 'heure_debut', 'heure_fin', 'motif',
            'statut', 'est_archivee', 'date_creation', 'date_validation',
        ]
        read_only_fields = ['demandeur', 'validateur', 'statut', 'est_archivee', 'date_creation', 'date_validation']

    def get_equipements_noms(self, obj):
        return [e.nom for e in obj.equipements.all()]