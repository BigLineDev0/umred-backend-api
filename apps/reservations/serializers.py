from rest_framework import serializers
from .models import Reservation


class ReservationSerializer(serializers.ModelSerializer):
    demandeur_nom = serializers.CharField(source='demandeur.__str__', read_only=True)
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)
    equipement_nom = serializers.CharField(source='equipement.nom', read_only=True, allow_null=True)

    class Meta:
        model = Reservation
        fields = [
            'id', 'demandeur', 'demandeur_nom', 'validateur',
            'laboratoire', 'laboratoire_nom', 'equipement', 'equipement_nom',
            'date', 'heure_debut', 'heure_fin', 'motif',
            'statut', 'est_archivee', 'date_creation', 'date_validation',
        ]
        read_only_fields = ['demandeur', 'validateur', 'statut', 'date_creation', 'est_archivee', 'date_validation']