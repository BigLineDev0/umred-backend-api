from rest_framework import serializers
from apps.organisations.isolation import ChampsOrganisationMixin
from .models import Equipement


class EquipementSerializer(ChampsOrganisationMixin, serializers.ModelSerializer):
    champs_organisation = {'laboratoire': 'organisation'}
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)
    nombre_utilisations = serializers.SerializerMethodField()

    class Meta:
        model = Equipement
        fields = [
            'id', 'laboratoire', 'laboratoire_nom', 'nom', 'description',
            'marque', 'modele', 'numero_serie', 'date_acquisition', 'statut', 'date_creation',
            'instructions_utilisation', 'consignes_securite', 'manuel_pdf',
            'necessite_validation', 'seuil_heures_maintenance', 'categorie', 'nombre_utilisations',
        ]
        read_only_fields = ['date_creation']
        
    def get_nombre_utilisations(self, obj) -> int:
        # Valeur annotée par EquipementViewSet quand elle existe.
        if hasattr(obj, 'nb_utilisations'):
            return obj.nb_utilisations
        from apps.reservations.models import STATUTS_BLOQUANTS
        return obj.reservations.filter(statut__in=STATUTS_BLOQUANTS).count()

    def validate_manuel_pdf(self, value):
        if value and value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("Le fichier ne doit pas dépasser 10 Mo.")
        if value and not value.name.lower().endswith('.pdf'):
            raise serializers.ValidationError("Seuls les fichiers PDF sont acceptés.")
        # L'extension se renomme facilement : on vérifie aussi la « signature »
        # du fichier. Tout vrai PDF commence par les octets « %PDF- ».
        if value:
            debut = value.read(5)
            value.seek(0)
            if debut != b'%PDF-':
                raise serializers.ValidationError("Le fichier n'est pas un PDF valide.")
        return value