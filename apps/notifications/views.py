from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from .models import Notification
from .serializers import NotificationSerializer


class NotificationPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        qs = Notification.objects.filter(destinataire=self.request.user)
        type_ = self.request.query_params.get('type')
        lu = self.request.query_params.get('lu')
        if type_:
            qs = qs.filter(type=type_)
        if lu is not None:
            qs = qs.filter(lu=(lu == 'true'))
        return qs

    @action(detail=True, methods=['post'])
    def marquer_lue(self, request, pk=None):
        notification = self.get_object()
        notification.marquer_comme_lue()
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=['post'])
    def tout_marquer_lu(self, request):
        self.get_queryset().filter(lu=False).update(lu=True)
        return Response({'detail': 'Toutes les notifications ont été marquées comme lues.'})

    @action(detail=False, methods=['get'])
    def non_lues_count(self, request):
        # Compte TOUJOURS sur l'ensemble réel, sans tenir compte d'un
        # éventuel filtre ?type=/?lu= actif ailleurs — sinon le badge du
        # topbar afficherait un chiffre faux si l'utilisateur avait un
        # filtre sélectionné sur la page Notifications.
        count = Notification.objects.filter(destinataire=request.user, lu=False).count()
        return Response({'count': count})