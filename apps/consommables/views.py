from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError

from django.db import transaction

from apps.core.services import enregistrer as journaliser
from apps.core.utils import lire_id
from apps.notifications.services import notifier
from apps.notifications.models import TypeNotification
from apps.utilisateurs.models import Utilisateur, StatutCompte, Role

from .models import Consommable, MouvementStock, TypeMouvement
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
        qs = super().get_queryset().select_related('laboratoire')
        laboratoire_id = lire_id(self.request, 'laboratoire')
        statut = self.request.query_params.get('statut')
        if laboratoire_id:
            qs = qs.filter(laboratoire_id=laboratoire_id)
        # Uniquement en liste : pour les autres actions, get_object() a
        # besoin d'un QuerySet, pas d'une liste Python.
        if statut and self.action == 'list':
            # Filtre en Python car 'statut' est une propriété calculée,
            # pas un champ de base de données interrogeable directement.
            qs = [c for c in qs if c.statut == statut]
        return qs

    def perform_create(self, serializer):
        with transaction.atomic():
            instance = serializer.save()
            # Le stock de départ est lui aussi tracé : l'historique des
            # mouvements permet ainsi de retrouver la quantité à tout moment.
            if instance.quantite_stock > 0:
                MouvementStock.objects.create(
                    consommable=instance, type=TypeMouvement.AJUSTEMENT,
                    quantite=instance.quantite_stock, utilisateur=self.request.user, motif='Stock initial',
                )
        journaliser(self.request.user, "Ajout d'un consommable", instance)

    def perform_update(self, serializer):
        # La quantité n'est pas écrite directement par le formulaire : elle
        # passe par ajuster_stock(), qui enregistre l'écart comme mouvement
        # AJUSTEMENT. Sinon on pourrait modifier le stock sans laisser de trace.
        nouvelle_quantite = serializer.validated_data.pop('quantite_stock', None)
        instance = serializer.save()
        if nouvelle_quantite is not None:
            try:
                instance.ajuster_stock(nouvelle_quantite, self.request.user)
            except DjangoValidationError as e:
                raise DRFValidationError(e.messages)
        journaliser(self.request.user, "Modification d'un consommable", instance)

    def perform_destroy(self, instance):
        journaliser(self.request.user, "Suppression d'un consommable",
                    description=f'Consommable supprimé : {instance.nom} ({instance.quantite_stock} {instance.get_unite_display()})')
        instance.delete()

    @action(detail=True, methods=['post'])
    def retirer(self, request, pk=None):
        """
        Ouvert à tout utilisateur connecté (rappel : c'est le chercheur qui
        manipule physiquement le produit, pas forcément le technicien).
        """
        consommable = self.get_object()
        serializer = RetirerStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        statut_avant = consommable.statut

        try:
            consommable.retirer_stock(
                quantite=serializer.validated_data['quantite'],
                utilisateur=request.user,
                motif=serializer.validated_data.get('motif', ''),
            )
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)

        journaliser(request.user, "Utilisation d'un consommable", consommable,
                    f"{serializer.validated_data['quantite']} {consommable.get_unite_display()}")

        self._alerter_si_necessaire(consommable, statut_avant)
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
        for c in Consommable.objects.select_related('laboratoire'):
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

    def _alerter_si_necessaire(self, consommable, statut_avant):
        # On n'alerte qu'au moment où le statut CHANGE (DISPONIBLE -> STOCK_FAIBLE,
        # STOCK_FAIBLE -> EPUISE), pas à chaque retrait sous le seuil : sinon
        # les techniciens recevraient une notification à chaque utilisation.
        if consommable.statut not in ['STOCK_FAIBLE', 'EPUISE'] or consommable.statut == statut_avant:
            return
        techniciens = Utilisateur.objects.filter(role=Role.TECHNICIEN, statut_compte=StatutCompte.ACTIF)
        libelle = 'épuisé' if consommable.statut == 'EPUISE' else 'stock faible'
        for technicien in techniciens:
            notifier(technicien, f'Alerte stock : {consommable.nom}',
                     f'{consommable.nom} est en {libelle} ({consommable.quantite_stock} {consommable.get_unite_display()} restant(s)).',
                     TypeNotification.SYSTEME, consommable)