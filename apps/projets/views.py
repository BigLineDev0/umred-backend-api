from rest_framework import permissions, status, viewsets
from rest_framework.response import Response
from apps.organisations.isolation import filtrer_par_organisation
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
        from django.db.models import Q

        user = self.request.user
        qs = filtrer_par_organisation(super().get_queryset(), user, 'responsable__organisation')
        if user.role == Role.ADMIN:
            return qs
        # Un étudiant voit aussi les projets de son encadrant (pour y rattacher ses réservations).
        return qs.filter(Q(responsable=user) | Q(responsable_id=user.encadrant_id) if user.encadrant_id else Q(responsable=user))

    def create(self, request, *args, **kwargs):
        # Le responsable est toujours l'utilisateur connecté (jamais lu dans
        # la requête) et la priorité reste NORMALE : seul un admin peut
        # l'élever ensuite, car elle influe sur l'arbitrage des conflits.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        projet = serializer.save(responsable=request.user)
        # Réponse complète (id, priorité…) : le formulaire de réservation
        # présélectionne aussitôt le projet créé.
        return Response(ProjetSerializer(projet).data, status=status.HTTP_201_CREATED)