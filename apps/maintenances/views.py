from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from apps.core.services import enregistrer as journaliser
from apps.core.utils import lire_date, lire_id
from rest_framework import serializers

from apps.notifications.services import notifier
from apps.equipements.services import prevenir_indisponibilite, reservations_impactees
from apps.organisations.isolation import filtrer_par_organisation
from apps.notifications.models import TypeNotification
from apps.utilisateurs.models import Utilisateur, StatutCompte, Role

from .models import Maintenance, StatutMaintenance
from .serializers import MaintenanceSerializer, SignalementPanneSerializer, ClotureMaintenanceSerializer
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta


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
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'demarrer', 'prendre_en_charge', 'cloturer', 'annuler']:
            return [EstTechnicienOuAdmin()]
        return super().get_permissions()
    
    def get_queryset(self):
        user = self.request.user
        equipement_id = lire_id(self.request, 'equipement')

        base = filtrer_par_organisation(Maintenance.objects.all(), user, 'equipement__laboratoire__organisation')
        if user.role == Role.ADMIN or equipement_id:
            qs = base
        else:
            # Un technicien voit ses propres interventions assignées, PLUS
            # toutes les pannes "signalées" mais pas encore prises en charge
            # (technicien=None) — c'est la file d'attente commune que
            # n'importe quel technicien doit pouvoir consulter et récupérer.
            qs = base.filter(Q(technicien=user) | Q(statut=StatutMaintenance.SIGNALEE))

        statut = self.request.query_params.get('statut')
        type_ = self.request.query_params.get('type')
        date_debut = lire_date(self.request, 'date_debut')
        date_fin = lire_date(self.request, 'date_fin')

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
        return qs.select_related('equipement__laboratoire', 'technicien', 'signale_par')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        maintenance = Maintenance(**serializer.validated_data, technicien=request.user)
        try:
            maintenance.planifier()
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        
        # Enregistrement de logs
        journaliser(request.user, 'Planification de maintenance', maintenance)

        # Les réservations prévues le jour de l'intervention sont compromises :
        # leurs demandeurs sont prévenus (avec un équipement équivalent si possible).
        jour = timezone.localtime(maintenance.date_planifiee).date()
        impactees = reservations_impactees(maintenance.equipement, jusqu_au=jour, depuis=jour)
        prevenir_indisponibilite(
            maintenance.equipement, impactees,
            f"Une maintenance est planifiée sur {maintenance.equipement.nom} le {jour:%d/%m/%Y}.",
        )

        donnees = self.get_serializer(maintenance).data
        donnees['reservations_impactees'] = len(impactees)
        return Response(donnees, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def signaler_panne(self, request):
        serializer = SignalementPanneSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        try:
            maintenance = Maintenance.creer_depuis_signalement(
                equipement=serializer.validated_data['equipement'],
                description=serializer.validated_data['description'],
                signale_par=request.user,
            )
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        
        # Enregistrement de logs
        journaliser(request.user, 'Signalement de panne', maintenance)
        
        # Les réservations des 14 prochains jours sur l'équipement sont
        # compromises tant que la panne n'est pas réparée.
        impactees = reservations_impactees(
            maintenance.equipement, jusqu_au=timezone.localdate() + timedelta(days=14),
        )
        prevenir_indisponibilite(
            maintenance.equipement, impactees, f"Une panne a été signalée sur {maintenance.equipement.nom}.",
        )

        # Envoi notification aux techniciens de l'établissement
        techniciens = Utilisateur.objects.filter(
            role=Role.TECHNICIEN, statut_compte=StatutCompte.ACTIF,
            organisation_id=maintenance.equipement.laboratoire.organisation_id,
        )
        impact = f' {len(impactees)} réservation(s) à venir sont concernées.' if impactees else ''
        for technicien in techniciens:
            notifier(technicien, 'Panne signalée',
                    f'{request.user} a signalé une panne sur {maintenance.equipement}.{impact}',
                    TypeNotification.MAINTENANCE, maintenance)

        donnees = MaintenanceSerializer(maintenance).data
        donnees['reservations_impactees'] = len(impactees)
        return Response(donnees, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def demarrer(self, request, pk=None):
        maintenance = self.get_object()
        try:
            maintenance.demarrer()
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        
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
            raise DRFValidationError(e.messages)
        
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
        try:
            maintenance.annuler()
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)

        # Enregistrement de logs
        journaliser(request.user, 'Annulation de maintenance', maintenance)
        
        return Response(self.get_serializer(maintenance).data)
    
    @action(detail=True, methods=['post'], permission_classes=[EstTechnicienOuAdmin])
    def prendre_en_charge(self, request, pk=None):
        maintenance = self.get_object()
        # DateTimeField DRF : vérifie le format et renvoie un datetime avec
        # fuseau horaire (au lieu de passer la chaîne brute au modèle).
        champ = serializers.DateTimeField()
        if not request.data.get('date_planifiee'):
            raise DRFValidationError({'date_planifiee': "Ce champ est obligatoire."})
        try:
            date_planifiee = champ.to_internal_value(request.data['date_planifiee'])
        except serializers.ValidationError as e:
            raise DRFValidationError({'date_planifiee': e.detail})

        try:
            maintenance.prendre_en_charge(technicien=request.user, date_planifiee=date_planifiee)
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)

        journaliser(request.user, 'Prise en charge de maintenance', maintenance)
        return Response(self.get_serializer(maintenance).data)