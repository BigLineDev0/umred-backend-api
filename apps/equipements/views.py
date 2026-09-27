from rest_framework.decorators import action
from rest_framework import viewsets, permissions, filters

from apps.equipements.services import evaluer_usure
from .models import Equipement
from .serializers import EquipementSerializer
from apps.utilisateurs.models import Role
from rest_framework.response import Response
from apps.core.services import enregistrer as journaliser
from apps.core.utils import lire_id, SuppressionImpossible


class EstTechnicienOuAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [Role.TECHNICIEN, Role.ADMIN]


class EquipementViewSet(viewsets.ModelViewSet):
    queryset = Equipement.objects.all()
    serializer_class = EquipementSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['nom', 'numero_serie', 'marque', 'modele']

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [EstTechnicienOuAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset().select_related('laboratoire')
        laboratoire_id = lire_id(self.request, 'laboratoire')
        statut = self.request.query_params.get('statut')
        
        if laboratoire_id:
            qs = qs.filter(laboratoire_id=laboratoire_id)
        if statut:
            qs = qs.filter(statut=statut)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, "Création d'équipement", instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, "Modification d'équipement", instance)

    def perform_destroy(self, instance):
        # on_delete=CASCADE effacerait aussi les maintenances de
        # l'équipement : on refuse dès qu'il a un historique.
        if instance.reservations.exists() or instance.maintenances.exists():
            raise SuppressionImpossible(
                "Cet équipement a un historique de réservations ou de maintenances : "
                "passez-le plutôt au statut « Hors service »."
            )
        journaliser(self.request.user, "Suppression d'équipement",
                    description=f'Équipement supprimé : {instance.nom}')
        instance.delete()
        
    

    @action(detail=True, methods=['get'])
    def alerte_usure(self, request, pk=None):
        equipement = self.get_object()
        alerte = evaluer_usure(equipement)
        if alerte is None:
            return Response({'niveau': None})
        return Response({
            'niveau': alerte.niveau,
            'message': alerte.message,
            'heures_cumulees': alerte.heures_cumulees,
            'seuil': alerte.seuil,
            'pannes_recentes': alerte.pannes_recentes,
        })


    @action(detail=False, methods=['get'])
    def alertes_usure_actives(self, request):
        """
        Liste globale, pour un futur tableau de bord technicien — tous les
        équipements ayant au moins une alerte active, triés par sévérité.
        """
        resultats = []
        for e in Equipement.objects.exclude(statut='HORS_SERVICE').select_related('laboratoire'):
            alerte = evaluer_usure(e)
            if alerte:
                resultats.append({
                    'equipement_id': e.id, 'equipement_nom': e.nom,
                    'laboratoire_nom': e.laboratoire.nom,
                    'niveau': alerte.niveau, 'message': alerte.message,
                })
        resultats.sort(key=lambda r: r['niveau'] == 'critique', reverse=True)
        return Response(resultats)
    
    