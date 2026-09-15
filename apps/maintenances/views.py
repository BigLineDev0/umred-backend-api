from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from apps.core.services import enregistrer as journaliser

from apps.notifications.services import notifier
from apps.notifications.models import TypeNotification
from apps.utilisateurs.models import Utilisateur, StatutCompte

from .models import Maintenance
from .serializers import MaintenanceSerializer, SignalementPanneSerializer, ClotureMaintenanceSerializer
from apps.utilisateurs.models import Role


class EstTechnicienOuAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [Role.TECHNICIEN, Role.ADMIN]


class MaintenanceViewSet(viewsets.ModelViewSet):
    queryset = Maintenance.objects.all()
    serializer_class = MaintenanceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        # Planifier, modifier, démarrer, clôturer, supprimer : réservé aux techniciens/admin.
        # Signaler une panne reste ouvert à tout utilisateur authentifié — cohérent
        # avec le tableau des besoins fonctionnels (Chercheur, Étudiant peuvent signaler).
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'demarrer', 'cloturer']:
            return [EstTechnicienOuAdmin()]
        return super().get_permissions()
    
    def get_queryset(self):
        qs = super().get_queryset()
        equipement_id = self.request.query_params.get('equipement')
        statut = self.request.query_params.get('statut')
        type_ = self.request.query_params.get('type')
        date_debut = self.request.query_params.get('date_debut')
        
        date_fin = self.request.query_params.get('date_fin')
        
        if date_debut:
            qs = qs.filter(date_planifiee__date__gte=date_debut)
        if date_fin:
            qs = qs.filter(date_planifiee__date__lte=date_fin)
            
        if equipement_id:
            qs = qs.filter(equipement_id=equipement_id)
        if statut:
            qs = qs.filter(statut=statut)
        if type_:
            qs = qs.filter(type=type_)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        maintenance = Maintenance(**serializer.validated_data, technicien=request.user)
        try:
            maintenance.planifier()
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages if hasattr(e, 'messages') else str(e))
        
        # Enregistrement de logs
        journaliser(request.user, 'Planification de maintenance', maintenance)
            
        return Response(self.get_serializer(maintenance).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def signaler_panne(self, request):
        serializer = SignalementPanneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        maintenance = Maintenance.creer_depuis_signalement(
            equipement=serializer.validated_data['equipement'],
            description=serializer.validated_data['description'],
            signale_par=request.user,
        )
        
        # Enregistrement de logs
        journaliser(request.user, 'Signalement de panne', maintenance)
        
        # Envoi notification aux techniciens
        techniciens = Utilisateur.objects.filter(role=Role.TECHNICIEN, statut_compte=StatutCompte.ACTIF)
        for technicien in techniciens:
            notifier(technicien, 'Panne signalée',
                    f'{request.user} a signalé une panne sur {maintenance.equipement}.',
                    TypeNotification.MAINTENANCE, maintenance)
            
        return Response(MaintenanceSerializer(maintenance).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def demarrer(self, request, pk=None):
        maintenance = self.get_object()
        try:
            maintenance.demarrer()
        except DjangoValidationError as e:
            raise DRFValidationError(str(e))
        
        # Enregistement de logs
        journaliser(request.user, 'Démarrage de maintenance', maintenance)
        
        return Response(self.get_serializer(maintenance).data)

    @action(detail=True, methods=['post'])
    def cloturer(self, request, pk=None):
        serializer = ClotureMaintenanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        maintenance = self.get_object()
        try:
            maintenance.cloturer(rapport=serializer.validated_data['rapport'])
        except DjangoValidationError as e:
            raise DRFValidationError(str(e))
        
        # Enregistrement logs
        journaliser(request.user, 'Clôture de maintenance', maintenance,
                    serializer.validated_data['rapport'])
        
        # Notificatier les utilisateurs pour la disponible des equipements
        if maintenance.signale_par:
            notifier(maintenance.signale_par, 'Équipement de nouveau disponible',
                    f'La maintenance sur {maintenance.equipement} est terminée.',
                    TypeNotification.MAINTENANCE, maintenance)
            
        return Response(self.get_serializer(maintenance).data)

    @action(detail=True, methods=['post'])
    def annuler(self, request, pk=None):
        maintenance = self.get_object()
        maintenance.annuler()
        
        # Enregistrement de logs
        journaliser(request.user, 'Annulation de maintenance', maintenance)
        
        return Response(self.get_serializer(maintenance).data)