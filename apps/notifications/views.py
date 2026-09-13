from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Chacun ne voit que ses propres notifications, jamais celles des autres.
        return Notification.objects.filter(destinataire=self.request.user)

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
        return Response({'count': self.get_queryset().filter(lu=False).count()})