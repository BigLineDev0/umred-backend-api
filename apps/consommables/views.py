from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.core.services import enregistrer as journaliser
from apps.notifications.services import notifier
from apps.notifications.models import TypeNotification
from apps.utilisateurs.models import Utilisateur, StatutCompte, Role

from .models import Consommable, MouvementStock
from .serializers import (
    ConsommableSerializer, MouvementStockSerializer,
    RetirerStockSerializer, ReapprovisionnerSerializer,
)


class EstTechnicienOuAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [Role.TECHNICIEN, Role.ADMIN]


class ConsommableViewSet(viewsets.ModelViewSet):
    queryset = Consommable.objects.all()
    serializer_class = ConsommableSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        # La consultation reste ouverte à tous les rôles connectés (un
        # chercheur doit pouvoir vérifier un stock avant de réserver) —
        # seule la gestion de l'inventaire lui-même est restreinte.
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'reapprovisionner']:
            return [EstTechnicienOuAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        laboratoire_id = self.request.query_params.get('laboratoire')
        statut = self.request.query_params.get('statut')
        if laboratoire_id:
            qs = qs.filter(laboratoire_id=laboratoire_id)
        if statut:
            # Filtre en Python car 'statut' est une propriété calculée,
            # pas un champ de base de données interrogeable directement.
            qs = [c for c in qs if c.statut == statut]
        return qs

    def perform_create(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, "Ajout d'un consommable", instance)

    @action(detail=True, methods=['post'])
    def retirer(self, request, pk=None):
        """
        Ouvert à tout utilisateur connecté (rappel : c'est le chercheur qui
        manipule physiquement le produit, pas forcément le technicien).
        """
        consommable = self.get_object()
        serializer = RetirerStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            consommable.retirer_stock(
                quantite=serializer.validated_data['quantite'],
                utilisateur=request.user,
                motif=serializer.validated_data.get('motif', ''),
            )
        except DjangoValidationError as e:
            raise DRFValidationError(str(e))

        journaliser(request.user, "Utilisation d'un consommable", consommable,
                    f"{serializer.validated_data['quantite']} {consommable.get_unite_display()}")

        self._alerter_si_necessaire(consommable)
        return Response(self.get_serializer(consommable).data)

    @action(detail=True, methods=['post'], permission_classes=[EstTechnicienOuAdmin])
    def reapprovisionner(self, request, pk=None):
        consommable = self.get_object()
        serializer = ReapprovisionnerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        consommable.reapprovisionner(
            quantite=serializer.validated_data['quantite'],
            utilisateur=request.user,
            motif=serializer.validated_data.get('motif', ''),
        )
        journaliser(request.user, "Réapprovisionnement d'un consommable", consommable,
                    f"{serializer.validated_data['quantite']} {consommable.get_unite_display()}")

        return Response(self.get_serializer(consommable).data)

    @action(detail=True, methods=['get'])
    def mouvements(self, request, pk=None):
        consommable = self.get_object()
        mouvements = consommable.mouvements.all()[:50]
        return Response(MouvementStockSerializer(mouvements, many=True).data)

    @action(detail=False, methods=['get'])
    def alertes_actives(self, request):
        """
        Même patron que alertes_usure_actives côté équipements : une liste
        globale, triée par urgence, pour un futur affichage dashboard.
        """
        resultats = []
        for c in Consommable.objects.all():
            if c.statut in ['STOCK_FAIBLE', 'EPUISE'] or c.peremption_proche or c.statut == 'PERIME':
                resultats.append({
                    'consommable_id': c.id, 'nom': c.nom,
                    'laboratoire_nom': c.laboratoire.nom,
                    'statut': c.statut, 'peremption_proche': c.peremption_proche,
                    'quantite_stock': str(c.quantite_stock), 'unite': c.get_unite_display(),
                })
        ordre = {'PERIME': 0, 'EPUISE': 1, 'STOCK_FAIBLE': 2, 'DISPONIBLE': 3}
        resultats.sort(key=lambda r: ordre.get(r['statut'], 9))
        return Response(resultats)

    def _alerter_si_necessaire(self, consommable):
        if consommable.statut not in ['STOCK_FAIBLE', 'EPUISE']:
            return
        techniciens = Utilisateur.objects.filter(role=Role.TECHNICIEN, statut_compte=StatutCompte.ACTIF)
        libelle = 'épuisé' if consommable.statut == 'EPUISE' else 'stock faible'
        for technicien in techniciens:
            notifier(technicien, f'Alerte stock : {consommable.nom}',
                     f'{consommable.nom} est en {libelle} ({consommable.quantite_stock} {consommable.get_unite_display()} restant(s)).',
                     TypeNotification.SYSTEME, consommable)