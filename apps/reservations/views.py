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
    queryset = Reservation.objects.all()   # ajouté — requis par DRF
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action == 'destroy':
            return [EstAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        est_superviseur = user.role in [Role.ADMIN, Role.TECHNICIEN, Role.CHERCHEUR]
        laboratoire_id = self.request.query_params.get('laboratoire')
        date_debut = self.request.query_params.get('date_debut')
        date_fin = self.request.query_params.get('date_fin')
        statut = self.request.query_params.get('statut')
        inclure_toutes = self.request.query_params.get('all') == 'true'

        if self.action == 'list':
            if inclure_toutes and est_superviseur:
                qs = Reservation.objects.all()
            elif laboratoire_id:
                qs = Reservation.objects.filter(statut=StatutReservation.VALIDEE)
            else:
                qs = Reservation.objects.filter(demandeur=user)

            # L'exclusion des archivées ne concerne QUE le parcours de liste
            # (naviguer/afficher). Une action ciblée par ID (valider, annuler,
            # et surtout désarchiver) doit toujours pouvoir atteindre son objet,
            # peu importe son statut d'archivage.
            if self.request.query_params.get('archivees') != 'true':
                qs = qs.exclude(est_archivee=True)

            if self.request.query_params.get('a_venir') == 'true':
                from django.utils import timezone
                qs = qs.filter(date__gte=timezone.now().date())
        else:
            qs = Reservation.objects.all() if est_superviseur else Reservation.objects.filter(demandeur=user)

        if laboratoire_id:
            qs = qs.filter(laboratoire_id=laboratoire_id)
        if date_debut:
            qs = qs.filter(date__gte=date_debut)
        if date_fin:
            qs = qs.filter(date__lte=date_fin)
        if statut:
            qs = qs.filter(statut=statut)

        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        equipements = data.pop('equipements', [])  # liste d'instances Equipement, déjà résolues par DRF

        reservation = Reservation(
            demandeur=request.user,
            laboratoire=data['laboratoire'],
            date=data['date'],
            heure_debut=data['heure_debut'],
            heure_fin=data['heure_fin'],
            motif=data['motif'],
        )

        try:
            reservation.creer(equipements_ids=[e.id for e in equipements])
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages if hasattr(e, 'messages') else str(e))

        journaliser(request.user, 'Création de réservation', reservation,
                    f'Statut initial : {reservation.statut}')

        if reservation.statut == StatutReservation.EN_ATTENTE:
            validateurs = Utilisateur.objects.filter(
                role__in=[Role.TECHNICIEN, Role.CHERCHEUR], statut_compte=StatutCompte.ACTIF
            )
            for validateur in validateurs:
                notifier(validateur, 'Nouvelle demande de réservation',
                        f'{request.user} a soumis une demande pour le {reservation.date}.',
                        TypeNotification.RESERVATION, reservation)

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