from rest_framework import serializers
from apps.utilisateurs.models import Role
from .models import Reservation

# Rôles qui supervisent les réservations et voient donc tous leurs détails.
ROLES_SUPERVISEURS = [Role.ADMIN, Role.TECHNICIEN, Role.CHERCHEUR]

# Champs masqués quand on consulte la réservation de quelqu'un d'autre sans
# être superviseur (ex. un étudiant qui regarde le planning d'un labo) :
# il doit voir que le créneau est pris et par qui, mais pas l'email, le
# motif ni le projet de recherche du demandeur.
CHAMPS_PRIVES = ['demandeur_email', 'motif', 'projet', 'projet_nom', 'rappel_24h_envoye', 'rappel_1h_envoye']


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
        read_only_fields = [
            'demandeur', 'validateur', 'statut', 'est_archivee', 'date_creation', 'date_validation',
            'rappel_24h_envoye', 'rappel_1h_envoye',
        ]

    def get_equipements_noms(self, obj):
        return [e.nom for e in obj.equipements.all()]

    def validate(self, attrs):
        # Vérifié dès le serializer (et pas seulement dans Reservation.clean)
        # car la vue calcule les conflits et les alternatives AVANT d'appeler
        # creer() : un créneau incohérent fausserait ce calcul.
        if attrs['heure_fin'] <= attrs['heure_debut']:
            raise serializers.ValidationError("L'heure de fin doit être après l'heure de début.")
        return attrs

    def validate_projet(self, projet):
        # La priorité du projet sert à arbitrer les conflits : sans ce
        # contrôle, n'importe qui pourrait rattacher sa demande au projet
        # CRITIQUE d'un autre pour passer devant. Seul le responsable du
        # projet (ou l'admin) peut s'en réclamer.
        user = self.context['request'].user
        if projet and projet.responsable_id != user.id and user.role != Role.ADMIN:
            raise serializers.ValidationError("Vous ne pouvez rattacher une réservation qu'à vos propres projets.")
        return projet

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if request and request.user.role not in ROLES_SUPERVISEURS and instance.demandeur_id != request.user.id:
            for champ in CHAMPS_PRIVES:
                data.pop(champ, None)
        return data
