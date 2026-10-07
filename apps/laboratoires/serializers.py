from rest_framework import serializers
from apps.core.validation import valider_nom_commun, valider_texte_long
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