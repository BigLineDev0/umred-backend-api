from django.db.models import Count, Q
from rest_framework import viewsets, permissions
from apps.core.utils import SuppressionImpossible
from apps.equipements.models import StatutEquipement
from .models import Laboratoire
from .serializers import LaboratoireSerializer
from apps.utilisateurs.models import Role
from apps.core.services import enregistrer as journaliser


class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == Role.ADMIN


class LaboratoireViewSet(viewsets.ModelViewSet):
    queryset = Laboratoire.objects.all()
    serializer_class = LaboratoireSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        # Tout le monde peut consulter (list, retrieve) — indispensable pour
        # que le formulaire de réservation Angular affiche la liste des labos.
        # Seul l'admin peut créer/modifier/supprimer un laboratoire.
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [EstAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        # Les deux compteurs du serializer sont calculés par la base en une
        # seule requête (COUNT ... GROUP BY) au lieu de 2 requêtes par labo.
        return super().get_queryset().select_related('responsable').annotate(
            nb_equipements=Count('equipements'),
            nb_equipements_disponibles=Count('equipements', filter=Q(equipements__statut=StatutEquipement.DISPONIBLE)),
        )

    def perform_create(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, 'Création de laboratoire', instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, 'Modification de laboratoire', instance)

    def perform_destroy(self, instance):
        # CASCADE supprimerait équipements, réservations et consommables du
        # labo : on refuse s'il en contient, il faut le passer « Indisponible ».
        if instance.reservations.exists() or instance.equipements.exists() or instance.consommables.exists():
            raise SuppressionImpossible(
                "Ce laboratoire contient des équipements, consommables ou réservations : "
                "passez-le plutôt au statut « Indisponible »."
            )
        journaliser(self.request.user, 'Suppression de laboratoire',
                    description=f'Laboratoire supprimé : {instance.nom}')
        instance.delete()