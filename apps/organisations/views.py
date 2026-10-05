import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import generics, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.services import enregistrer as journaliser
from apps.utilisateurs.models import JetonDefinitionMotDePasse, Role, StatutCompte, Utilisateur
from apps.utilisateurs.services import envoyer_lien_definition_mdp, revoquer_sessions

from .isolation import est_super_admin
from .models import Organisation
from .serializers import (
    CreationOrganisationSerializer, OrganisationPlateformeSerializer, OrganisationPubliqueSerializer,
    OrganisationSerializer,
)

logger = logging.getLogger(__name__)


class EstSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and est_super_admin(request.user)


class OrganisationsPubliquesView(generics.ListAPIView):
    """Établissements actifs, pour le choix à l'inscription (visiteur non connecté)."""
    serializer_class = OrganisationPubliqueSerializer
    queryset = Organisation.objects.filter(est_active=True)
    permission_classes = [permissions.AllowAny]
    # Aucune authentification : un vieux token resté dans le navigateur ne
    # doit pas faire échouer la page d'inscription en 401.
    authentication_classes = []
    pagination_class = None


class OrganisationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                          mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Console de l'éditeur (super-admin) : liste des établissements clients
    avec leurs indicateurs d'usage, création, suspension. Pas de
    suppression : un établissement se suspend, ses données restent.
    """
    permission_classes = [EstSuperAdmin]

    def get_permissions(self):
        if self.action == 'courante':
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        il_y_a_30_jours = timezone.localdate() - timedelta(days=30)
        return Organisation.objects.annotate(
            nb_laboratoires=Count('laboratoires', distinct=True),
            nb_utilisateurs=Count('utilisateurs', distinct=True),
            nb_equipements=Count('laboratoires__equipements', distinct=True),
            nb_reservations_30j=Count(
                'laboratoires__reservations', distinct=True,
                filter=Q(laboratoires__reservations__date__gte=il_y_a_30_jours),
            ),
        )

    def get_serializer_class(self):
        return CreationOrganisationSerializer if self.action == 'create' else OrganisationPlateformeSerializer

    def create(self, request, *args, **kwargs):
        """
        Crée l'établissement ET son premier administrateur, invité par
        email à choisir son mot de passe (même flux que les comptes créés
        par un admin). L'éditeur ne connaît jamais ce mot de passe.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        admin_champs = {k: data.pop(k) for k in ['admin_email', 'admin_nom', 'admin_prenom']}

        with transaction.atomic():
            organisation = Organisation.objects.create(**data)
            admin = Utilisateur.objects.create_user(
                email=admin_champs['admin_email'], password=None,
                nom=admin_champs['admin_nom'], prenom=admin_champs['admin_prenom'],
                role=Role.ADMIN, organisation=organisation,
            )
            jeton = JetonDefinitionMotDePasse.generer_pour(admin)
        try:
            envoyer_lien_definition_mdp(admin, jeton.jeton)
        except Exception:
            logger.exception("Échec d'envoi de l'invitation à %s", admin.email)

        journaliser(request.user, "Création d'un établissement", organisation, f'Admin : {admin.email}')
        return Response(OrganisationPlateformeSerializer(self.get_queryset().get(pk=organisation.pk)).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def suspendre(self, request, pk=None):
        organisation = self.get_object()
        organisation.est_active = False
        organisation.save(update_fields=['est_active'])
        # Les sessions ouvertes sont fermées : sans cela, les utilisateurs
        # déjà connectés pourraient continuer à renouveler leur token.
        for utilisateur in organisation.utilisateurs.all():
            revoquer_sessions(utilisateur)
        journaliser(request.user, "Suspension d'un établissement", organisation)
        return Response(OrganisationPlateformeSerializer(self.get_queryset().get(pk=organisation.pk)).data)

    @action(detail=True, methods=['post'])
    def reactiver(self, request, pk=None):
        organisation = self.get_object()
        organisation.est_active = True
        organisation.save(update_fields=['est_active'])
        journaliser(request.user, "Réactivation d'un établissement", organisation)
        return Response(OrganisationPlateformeSerializer(self.get_queryset().get(pk=organisation.pk)).data)

    @action(detail=False, methods=['get'])
    def statistiques(self, request):
        """Indicateurs globaux de la plateforme pour le tableau de bord de l'éditeur."""
        from apps.reservations.models import Reservation

        aujourd_hui = timezone.localdate()
        # Nouvelles organisations et réservations sur les 6 derniers mois.
        evolution = []
        annee, mois = aujourd_hui.year, aujourd_hui.month
        for _ in range(6):
            evolution.append({
                'mois': f'{annee}-{mois:02d}',
                'reservations': Reservation.objects.filter(date__year=annee, date__month=mois).count(),
                'nouveaux_utilisateurs': Utilisateur.objects.filter(date_creation__year=annee, date_creation__month=mois).count(),
            })
            mois -= 1
            if mois == 0:
                annee, mois = annee - 1, 12
        evolution.reverse()
        return Response({
            'organisations_total': Organisation.objects.count(),
            'organisations_actives': Organisation.objects.filter(est_active=True).count(),
            'utilisateurs_actifs': Utilisateur.objects.filter(statut_compte=StatutCompte.ACTIF).exclude(role=Role.SUPER_ADMIN).count(),
            'reservations_30j': Reservation.objects.filter(date__gte=aujourd_hui - timedelta(days=30)).count(),
            'evolution': evolution,
        })


    @action(detail=False, methods=['get', 'patch'])
    def courante(self, request):
        """
        GET : l'établissement de l'utilisateur (identité visuelle et règles,
        appliquées par le frontend au chargement). PATCH : son admin le
        configure.
        """
        organisation = request.user.organisation
        if organisation is None:
            return Response({'detail': "Aucun établissement rattaché à ce compte."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == 'GET':
            return Response(OrganisationSerializer(organisation, context={'request': request}).data)

        if request.user.role != Role.ADMIN:
            return Response({'detail': "Seul l'administrateur peut configurer l'établissement."},
                            status=status.HTTP_403_FORBIDDEN)
        serializer = OrganisationSerializer(organisation, data=request.data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        journaliser(request.user, "Configuration de l'établissement", organisation)
        return Response(serializer.data)
