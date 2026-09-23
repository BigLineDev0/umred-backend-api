from rest_framework import viewsets, permissions
from apps.utilisateurs.models import Role
from .models import Projet
from .serializers import ProjetSerializer, ProjetCreateSerializer


class PeutCreerProjet(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method != 'POST':
            return True
        return request.user.role in [Role.CHERCHEUR, Role.ADMIN]


class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role == Role.ADMIN


class ProjetViewSet(viewsets.ModelViewSet):
    queryset = Projet.objects.all()
    permission_classes = [permissions.IsAuthenticated, PeutCreerProjet]

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), EstAdmin()]
        return super().get_permissions()

    def get_serializer_class(self):
        return ProjetCreateSerializer if self.action == 'create' else ProjetSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        return qs if self.request.user.role == Role.ADMIN else qs.filter(responsable=self.request.user)

    def perform_create(self, serializer):
        serializer.save(responsable=self.request.user)