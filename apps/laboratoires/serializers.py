from rest_framework import serializers
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