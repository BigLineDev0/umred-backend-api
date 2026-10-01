from rest_framework import serializers
from apps.organisations.isolation import ChampsOrganisationMixin
from apps.utilisateurs.models import Role
from .models import AlerteCreneau, Reservation

# Rôles qui supervisent les réservations et voient donc tous leurs détails.
ROLES_SUPERVISEURS = [Role.ADMIN, Role.TECHNICIEN, Role.CHERCHEUR]

# Champs masqués quand on consulte la réservation de quelqu'un d'autre sans
# être superviseur (ex. un étudiant qui regarde le planning d'un labo) :
# il doit voir que le créneau est pris et par qui, mais pas l'email, le
# motif ni le projet de recherche du demandeur.
CHAMPS_PRIVES = [
    'demandeur_email', 'motif', 'motif_refus', 'projet', 'projet_nom', 'rappel_24h_envoye', 'rappel_1h_envoye',
]


class ReservationSerializer(ChampsOrganisationMixin, serializers.ModelSerializer):
    # Seuls les laboratoires, équipements et projets de l'établissement de
    # l'utilisateur sont acceptés (voir apps.organisations.isolation).
    champs_organisation = {
        'laboratoire': 'organisation',
        'equipements': 'laboratoire__organisation',
        'projet': 'responsable__organisation',
    }

    demandeur_nom = serializers.CharField(source='demandeur.nom_complet', read_only=True)
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)
    demandeur_email = serializers.EmailField(source='demandeur.email', read_only=True)
    demandeur_role = serializers.CharField(source='demandeur.role', read_only=True)
    equipements_noms = serializers.SerializerMethodField()
    projet_nom = serializers.CharField(source='projet.nom', read_only=True, allow_null=True)
    # Calculé par le serveur (qui a l'heure de référence) : le frontend
    # n'affiche le bouton d'annulation que si l'action est réellement permise.
    annulable = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            'id', 'demandeur', 'demandeur_nom','demandeur_email', 'validateur',
            'laboratoire', 'laboratoire_nom', 'equipements', 'equipements_noms',
            'demandeur_role', 'date', 'heure_debut', 'heure_fin', 'motif',
            'statut', 'motif_refus', 'est_archivee', 'date_creation', 'date_validation',
            'projet', 'projet_nom',
            'rappel_24h_envoye', 'rappel_1h_envoye', 'annulable',
        ]
        read_only_fields = [
            'demandeur', 'validateur', 'statut', 'motif_refus', 'est_archivee', 'date_creation', 'date_validation',
            'rappel_24h_envoye', 'rappel_1h_envoye',
        ]

    def get_annulable(self, obj) -> bool:
        from .models import StatutReservation
        return obj.statut in [StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE] and not obj.creneau_commence()

    def get_equipements_noms(self, obj) -> list[str]:
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
        # Un étudiant peut aussi se réclamer des projets de son encadrant :
        # c'est dans ce cadre qu'il utilise le laboratoire.
        user = self.context['request'].user
        autorise = (
            projet is None or user.role == Role.ADMIN or projet.responsable_id == user.id
            or (user.encadrant_id and projet.responsable_id == user.encadrant_id)
        )
        if not autorise:
            raise serializers.ValidationError(
                "Vous ne pouvez rattacher une réservation qu'à vos projets ou à ceux de votre encadrant."
            )
        return projet

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if request and request.user.role not in ROLES_SUPERVISEURS and instance.demandeur_id != request.user.id:
            for champ in CHAMPS_PRIVES:
                data.pop(champ, None)
        return data


class AlerteCreneauSerializer(ChampsOrganisationMixin, serializers.ModelSerializer):
    champs_organisation = {
        'laboratoire': 'organisation',
        'equipements': 'laboratoire__organisation',
    }
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)
    equipements_noms = serializers.SerializerMethodField()

    class Meta:
        model = AlerteCreneau
        fields = [
            'id', 'laboratoire', 'laboratoire_nom', 'equipements', 'equipements_noms',
            'date', 'heure_debut', 'heure_fin', 'active', 'date_creation', 'date_notification',
        ]
        read_only_fields = ['active', 'date_creation', 'date_notification']

    def get_annulable(self, obj) -> bool:
        from .models import StatutReservation
        return obj.statut in [StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE] and not obj.creneau_commence()

    def get_equipements_noms(self, obj) -> list[str]:
        return [e.nom for e in obj.equipements.all()]

    def validate(self, attrs):
        from django.utils import timezone
        if attrs['heure_fin'] <= attrs['heure_debut']:
            raise serializers.ValidationError("L'heure de fin doit être après l'heure de début.")
        if attrs['date'] < timezone.localdate():
            raise serializers.ValidationError("Impossible de suivre un créneau déjà passé.")
        return attrs


class RefusSerializer(serializers.Serializer):
    motif = serializers.CharField(required=False, allow_blank=True, max_length=500)
