from rest_framework import serializers
from apps.core.validation import cle_unicite, valider_nom_commun, valider_texte_long
from apps.organisations.isolation import ChampsOrganisationMixin
from .models import Laboratoire


class LaboratoireSerializer(ChampsOrganisationMixin, serializers.ModelSerializer):
    champs_organisation = {'responsable': 'organisation'}
    responsable_nom = serializers.CharField(source='responsable.nom_complet', read_only=True, allow_null=True)
    nombre_equipements = serializers.SerializerMethodField()
    nombre_equipements_disponibles = serializers.SerializerMethodField()

    class Meta:
        model = Laboratoire
        fields = [
            'id', 'nom', 'description', 'localisation', 'capacite', 'statut',
            'photo', 'responsable', 'responsable_nom',
            'nombre_equipements', 'nombre_equipements_disponibles', 'date_creation',
        ]
        read_only_fields = ['date_creation']

    def validate_nom(self, value):
        return valider_nom_commun(value)

    def validate_localisation(self, value):
        return valider_nom_commun(value, min_len=2, max_len=150)

    def validate_description(self, value):
        # Obligatoire (min 10) côté application, cohérent avec le formulaire Angular.
        return valider_texte_long(value, max_len=500, min_len=10, obligatoire=True)

    def validate(self, attrs):
        # Unicité du nom par organisation, insensible à la casse/accents/espaces.
        # L'organisation vient de l'instance (modif) ou de l'utilisateur (création).
        nom = attrs.get('nom', getattr(self.instance, 'nom', None))
        if nom:
            if self.instance is not None:
                organisation_id = self.instance.organisation_id
            else:
                user = getattr(self.context.get('request'), 'user', None)
                organisation_id = getattr(user, 'organisation_id', None)
            qs = Laboratoire.objects.filter(organisation_id=organisation_id)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            cible = cle_unicite(nom)
            if any(cle_unicite(nom_existant) == cible for nom_existant in qs.values_list('nom', flat=True)):
                raise serializers.ValidationError(
                    {'nom': f"Un laboratoire nommé « {nom.strip()} » existe déjà dans votre établissement."}
                )
        return attrs

    # Valeurs pré-calculées par l'annotate() de LaboratoireViewSet quand
    # elles existent ; sinon (objet chargé ailleurs) on compte directement.
    def get_nombre_equipements(self, obj) -> int:
        if hasattr(obj, 'nb_equipements'):
            return obj.nb_equipements
        return obj.equipements.count()

    def get_nombre_equipements_disponibles(self, obj) -> int:
        if hasattr(obj, 'nb_equipements_disponibles'):
            return obj.nb_equipements_disponibles
        return obj.equipements.filter(statut='DISPONIBLE').count()