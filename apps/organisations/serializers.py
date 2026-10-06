from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.utilisateurs.serializers import verifier_email_unique
from .models import Organisation


class OrganisationPubliqueSerializer(serializers.ModelSerializer):
    """Ce qu'un visiteur non connecté peut voir (choix de l'établissement à l'inscription)."""
    class Meta:
        model = Organisation
        fields = ['id', 'nom', 'slug', 'ville', 'logo', 'couleur_primaire']


class OrganisationSerializer(serializers.ModelSerializer):
    """Configuration de l'établissement, modifiable par son admin."""
    class Meta:
        model = Organisation
        fields = [
            'id', 'nom', 'slug', 'ville', 'email_contact', 'logo', 'couleur_primaire', 'couleur_secondaire',
            'heure_ouverture', 'heure_fermeture', 'duree_min_reservation', 'duree_max_reservation',
            'delai_max_reservation_jours', 'est_active', 'date_creation',
        ]
        # Le slug et l'état d'abonnement relèvent de l'éditeur (super-admin).
        read_only_fields = ['slug', 'est_active', 'date_creation']

    def validate_logo(self, value):
        if value and value.size > 2 * 1024 * 1024:
            raise serializers.ValidationError("Le logo ne doit pas dépasser 2 Mo.")
        return value

    def validate(self, attrs):
        # Réutilise les contrôles du modèle (horaires, durées) sur l'objet
        # tel qu'il serait après modification.
        # Seuls les champs du modèle : à la création, attrs contient aussi
        # les champs du futur administrateur (admin_email...).
        champs = {f.name for f in Organisation._meta.fields}
        valeurs = {nom: getattr(self.instance, nom) for nom in champs} if self.instance else {}
        valeurs.update({nom: valeur for nom, valeur in attrs.items() if nom in champs})
        instance = Organisation(**valeurs)
        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return attrs


class OrganisationPlateformeSerializer(OrganisationSerializer):
    """Vue super-admin : indicateurs d'usage de chaque établissement client."""
    nb_laboratoires = serializers.IntegerField(read_only=True)
    nb_utilisateurs = serializers.IntegerField(read_only=True)
    nb_equipements = serializers.IntegerField(read_only=True)
    nb_reservations_30j = serializers.IntegerField(read_only=True)

    class Meta(OrganisationSerializer.Meta):
        fields = OrganisationSerializer.Meta.fields + [
            'nb_laboratoires', 'nb_utilisateurs', 'nb_equipements', 'nb_reservations_30j',
        ]
        read_only_fields = ['date_creation']


class CreationOrganisationSerializer(OrganisationSerializer):
    """Création d'un établissement client avec son premier administrateur."""
    admin_email = serializers.EmailField(write_only=True)
    admin_nom = serializers.CharField(write_only=True, max_length=100)
    admin_prenom = serializers.CharField(write_only=True, max_length=100)

    class Meta(OrganisationSerializer.Meta):
        fields = OrganisationSerializer.Meta.fields + ['admin_email', 'admin_nom', 'admin_prenom']
        read_only_fields = ['est_active', 'date_creation']

    def validate_admin_email(self, value):
        return verifier_email_unique(value)
