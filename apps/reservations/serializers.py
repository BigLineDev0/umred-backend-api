from rest_framework import serializers
from .models import Reservation

class ReservationSerializer(serializers.ModelSerializer):
    demandeur_nom = serializers.CharField(source='demandeur.nom_complet', read_only=True)
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)
    demandeur_email = serializers.EmailField(source='demandeur.email', read_only=True)
    demandeur_role = serializers.CharField(source='demandeur.role', read_only=True)
    equipements_noms = serializers.SerializerMethodField()
    projet_nom = serializers.CharField(source='projet.nom', read_only=True, allow_null=True)

    class Meta:
        model = Reservation
        fields = [
            'id', 'demandeur', 'demandeur_nom','demandeur_email', 'validateur',
            'laboratoire', 'laboratoire_nom', 'equipements', 'equipements_noms',
            'demandeur_role', 'date', 'heure_debut', 'heure_fin', 'motif',
            'statut', 'est_archivee', 'date_creation', 'date_validation',
            'projet', 'projet_nom',
            'rappel_24h_envoye', 'rappel_1h_envoye'
        ]
        read_only_fields = ['demandeur', 'validateur', 'statut', 'est_archivee', 'date_creation', 'date_validation']

    def get_equipements_noms(self, obj):
        return [e.nom for e in obj.equipements.all()]