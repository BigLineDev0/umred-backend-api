from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from apps.core.services import enregistrer as journaliser

from apps.notifications.services import notifier
from apps.notifications.models import TypeNotification
from apps.utilisateurs.models import Utilisateur, StatutCompte

from .models import Reservation
from apps.reservations.models import StatutReservation
from .serializers import ReservationSerializer
from apps.utilisateurs.models import Role


class EstValidateur(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [
            Role.TECHNICIEN, Role.CHERCHEUR, Role.ADMIN
        ]


class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == Role.ADMIN


class ReservationViewSet(viewsets.ModelViewSet):
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        # La suppression physique est réservée à l'admin, en dernier recours seulement
        if self.action == 'destroy':
            return [EstAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        inclure_archivees = self.request.query_params.get('archivees') == 'true'

        if user.role in [Role.ADMIN, Role.TECHNICIEN, Role.CHERCHEUR]:
            qs = Reservation.objects.all()
        else:
            qs = Reservation.objects.filter(demandeur=user)

        if not inclure_archivees:
            qs = qs.exclude(est_archivee=True)

        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reservation = Reservation(
            demandeur=request.user,
            laboratoire=serializer.validated_data['laboratoire'],
            equipement=serializer.validated_data.get('equipement'),
            date=serializer.validated_data['date'],
            heure_debut=serializer.validated_data['heure_debut'],
            heure_fin=serializer.validated_data['heure_fin'],
            motif=serializer.validated_data['motif'],
        )
        try:
            reservation.creer()
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages if hasattr(e, 'messages') else str(e))

        # Enregistrement de logs
        journaliser(request.user, 'Création de réservation', reservation,
                    f'Statut initial : {reservation.statut}')
        
        # Envoie notifications
        if reservation.statut == StatutReservation.EN_ATTENTE:
            validateurs = Utilisateur.objects.filter(
                role__in=[Role.TECHNICIEN, Role.CHERCHEUR], statut_compte=StatutCompte.ACTIF
            )
            for validateur in validateurs:
                notifier(
                    validateur, 'Nouvelle demande de réservation',
                    f'{request.user} a soumis une demande pour le {reservation.date}.',
                    TypeNotification.RESERVATION, reservation
                )

        return Response(self.get_serializer(reservation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[EstValidateur])
    def valider(self, request, pk=None):
        reservation = self.get_object()
        reservation.valider(validateur=request.user)
        
        # Enregistrement logs
        journaliser(request.user, 'Validation de réservation', reservation)
        
        # Envoie notifications
        notifier(reservation.demandeur, 'Réservation validée',
                 f'Votre réservation du {reservation.date} a été validée.',
                 TypeNotification.VALIDATION, reservation)
        return Response(self.get_serializer(reservation).data)

    @action(detail=True, methods=['post'], permission_classes=[EstValidateur])
    def refuser(self, request, pk=None):
        reservation = self.get_object()
        reservation.refuser(validateur=request.user)
        journaliser(request.user, 'Refus de réservation', reservation)
        
        # Envoie notifications
        notifier(reservation.demandeur, 'Réservation refusée',
                 f'Votre réservation du {reservation.date} a été refusée.',
                 TypeNotification.VALIDATION, reservation)
        return Response(self.get_serializer(reservation).data)

    @action(detail=True, methods=['post'])
    def annuler(self, request, pk=None):
        reservation = self.get_object()
        reservation.annuler()
        journaliser(request.user, 'Annulation de réservation', reservation)
        return Response(self.get_serializer(reservation).data)

    @action(detail=True, methods=['post'], permission_classes=[EstAdmin])
    def archiver(self, request, pk=None):
        reservation = self.get_object()
        reservation.archiver()
        journaliser(request.user, 'Archivage de réservation', reservation)
        return Response(self.get_serializer(reservation).data)

    @action(detail=True, methods=['post'], permission_classes=[EstAdmin])
    def desarchiver(self, request, pk=None):
        reservation = self.get_object()
        reservation.desarchiver()
        journaliser(request.user, 'Désarchivage de réservation', reservation)
        return Response(self.get_serializer(reservation).data)