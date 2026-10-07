from datetime import date
from decimal import Decimal

from rest_framework import serializers
from apps.core.validation import cle_unicite, valider_nom_commun, valider_reference
from apps.organisations.isolation import ChampsOrganisationMixin
from .models import Consommable, MouvementStock

QUANTITE_MAX = Decimal("1000000")


class ConsommableSerializer(ChampsOrganisationMixin, serializers.ModelSerializer):
    champs_organisation = {'laboratoire': 'organisation'}
    laboratoire_nom = serializers.CharField(source='laboratoire.nom', read_only=True)
    statut = serializers.CharField(read_only=True)
    peremption_proche = serializers.BooleanField(read_only=True)

    class Meta:
        model = Consommable
        fields = [
            'id', 'laboratoire', 'laboratoire_nom', 'nom', 'reference', 'unite',
            'quantite_stock', 'seuil_alerte', 'date_peremption', 'statut',
            'peremption_proche', 'date_creation',
        ]
        read_only_fields = ['date_creation']

    def validate_nom(self, value):
        return valider_nom_commun(value)

    def validate_reference(self, value):
        return valider_reference(value)

    def validate_quantite_stock(self, value):
        if value < 0:
            raise serializers.ValidationError("La quantité ne peut pas être négative.")
        if value > QUANTITE_MAX:
            raise serializers.ValidationError("Quantité trop élevée.")
        return value

    def validate_seuil_alerte(self, value):
        if value < 0:
            raise serializers.ValidationError("Le seuil d'alerte ne peut pas être négatif.")
        if value > QUANTITE_MAX:
            raise serializers.ValidationError("Seuil d'alerte trop élevé.")
        return value

    def validate_date_peremption(self, value):
        # Refusée seulement à la création : un consommable déjà périmé peut
        # exister en base et rester modifiable.
        if value and self.instance is None and value < date.today():
            raise serializers.ValidationError("La date de péremption ne peut pas être déjà passée.")
        return value

    def validate(self, attrs):
        laboratoire = attrs.get('laboratoire', getattr(self.instance, 'laboratoire', None))
        base = Consommable.objects.filter(laboratoire=laboratoire) if laboratoire else Consommable.objects.none()
        if self.instance is not None:
            base = base.exclude(pk=self.instance.pk)

        nom = attrs.get('nom', getattr(self.instance, 'nom', None))
        if nom and laboratoire:
            cible = cle_unicite(nom)
            if any(cle_unicite(n) == cible for n in base.values_list('nom', flat=True)):
                raise serializers.ValidationError(
                    {'nom': f"Un consommable nommé « {nom.strip()} » existe déjà dans ce laboratoire."}
                )

        reference = attrs.get('reference', getattr(self.instance, 'reference', None))
        if reference and laboratoire:
            cible = cle_unicite(reference)
            if any(cle_unicite(r) == cible for r in base.values_list('reference', flat=True) if r):
                raise serializers.ValidationError(
                    {'reference': f"La référence « {reference.strip()} » est déjà utilisée dans ce laboratoire."}
                )
        return attrs


class MouvementStockSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.CharField(source='utilisateur.nom_complet', read_only=True, allow_null=True)
    consommable_nom = serializers.CharField(source='consommable.nom', read_only=True)

    class Meta:
        model = MouvementStock
        fields = ['id', 'consommable', 'consommable_nom', 'type', 'quantite', 'utilisateur', 'utilisateur_nom', 'motif', 'date_mouvement']
        read_only_fields = ['utilisateur', 'date_mouvement']


class RetirerStockSerializer(serializers.Serializer):
    quantite = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    motif = serializers.CharField(required=False, allow_blank=True)


class ReapprovisionnerSerializer(serializers.Serializer):
    quantite = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    motif = serializers.CharField(required=False, allow_blank=True)