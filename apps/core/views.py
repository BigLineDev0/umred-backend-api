from rest_framework import viewsets, permissions
from .models import JournalActivite
from .serializers import JournalActiviteSerializer
from apps.utilisateurs.models import Role


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