from rest_framework import viewsets, permissions, filters
from .models import Equipement
from .serializers import EquipementSerializer
from apps.utilisateurs.models import Role
from apps.core.services import enregistrer as journaliser


class EstTechnicienOuAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [Role.TECHNICIEN, Role.ADMIN]


class EquipementViewSet(viewsets.ModelViewSet):
    queryset = Equipement.objects.all()
    serializer_class = EquipementSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['nom', 'numero_serie', 'marque', 'modele']

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [EstTechnicienOuAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        laboratoire_id = self.request.query_params.get('laboratoire')
        if laboratoire_id:
            qs = qs.filter(laboratoire_id=laboratoire_id)
            
        equipement_id = self.request.query_params.get('equipement')
        if equipement_id:
            qs = qs.filter(equipement_id=equipement_id)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, "Création d'équipement", instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, "Modification d'équipement", instance)

    def perform_destroy(self, instance):
        journaliser(self.request.user, "Suppression d'équipement",
                    description=f'Équipement supprimé : {instance.nom}')
        instance.delete()