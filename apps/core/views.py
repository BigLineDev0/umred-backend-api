from rest_framework import viewsets, permissions
from .models import JournalActivite
from .serializers import JournalActiviteSerializer
from apps.utilisateurs.models import Role

from django.db.models import Q
from rest_framework.pagination import PageNumberPagination


class JournalPagination(PageNumberPagination):
    page_size = 9
    page_size_query_param = 'page_size'
    max_page_size = 1000

class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == Role.ADMIN


class JournalActiviteViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ReadOnlyModelViewSet plutôt que ModelViewSet : un journal ne se crée
    et ne se modifie jamais via l'API, uniquement via journaliser().
    """
    queryset = JournalActivite.objects.all()
    serializer_class = JournalActiviteSerializer
    permission_classes = [EstAdmin]
    pagination_class = JournalPagination

    def get_queryset(self):
        qs = super().get_queryset()
        auteur_id = self.request.query_params.get('auteur')
        action = self.request.query_params.get('action')
        entite = self.request.query_params.get('entite')
        search = self.request.query_params.get('search')
        date_debut = self.request.query_params.get('date_debut')
        date_fin = self.request.query_params.get('date_fin')

        if auteur_id:
            qs = qs.filter(auteur_id=auteur_id)
        if action:
            qs = qs.filter(action__icontains=action)
        if entite:
            qs = qs.filter(entite_type__model=entite)
        if search:
            qs = qs.filter(Q(action__icontains=search) | Q(description__icontains=search))
        if date_debut:
            qs = qs.filter(date_heure__date__gte=date_debut)
        if date_fin:
            qs = qs.filter(date_heure__date__lte=date_fin)
        return qs