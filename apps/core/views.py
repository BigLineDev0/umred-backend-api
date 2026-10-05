from django.http import HttpResponse
from rest_framework import viewsets, permissions

from apps.equipements.models import Equipement
from .models import JournalActivite
from .serializers import JournalActiviteSerializer
from apps.utilisateurs.models import Role

from django.db.models import Q
from rest_framework.pagination import PageNumberPagination

from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.reservations.models import Reservation
from apps.laboratoires.models import Laboratoire

from datetime import datetime
from .utils import lire_date, lire_id
from .analytique import calculer_indicateurs
from .rapport_pdf import generer_rapport_pdf
from apps.organisations.isolation import filtrer_par_organisation

import openpyxl
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

# Filtres communs des rapports et du pilotage, documentés dans Swagger.
PARAMETRES_PERIODE = [
    OpenApiParameter('date_debut', OpenApiTypes.DATE, description='Début de période (AAAA-MM-JJ)'),
    OpenApiParameter('date_fin', OpenApiTypes.DATE, description='Fin de période (AAAA-MM-JJ)'),
    OpenApiParameter('laboratoire', OpenApiTypes.INT, description='Restreindre à un laboratoire'),
]
from openpyxl.styles import Font, PatternFill

ROLE_LABELS = {
    Role.ADMIN: 'Administrateur', Role.TECHNICIEN: 'Technicien',
    Role.CHERCHEUR: 'Enseignant-chercheur', Role.ETUDIANT: 'Étudiant',
}
class JournalPagination(PageNumberPagination):
    page_size = 9
    page_size_query_param = 'page_size'
    max_page_size = 1000

class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == Role.ADMIN


class JournalActiviteViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ReadOnlyModelViewSet plutôt que ModelViewSet : un journal ne se crée
    et ne se modifie jamais via l'API, uniquement via journaliser().
    """
    queryset = JournalActivite.objects.all()
    serializer_class = JournalActiviteSerializer
    permission_classes = [EstAdmin]
    pagination_class = JournalPagination

    def get_queryset(self):
        qs = filtrer_par_organisation(super().get_queryset(), self.request.user, 'auteur__organisation')
        qs = qs.select_related('auteur', 'entite_type')
        auteur_id = lire_id(self.request, 'auteur')
        action = self.request.query_params.get('action')
        entite = self.request.query_params.get('entite')
        search = self.request.query_params.get('search')
        date_debut = lire_date(self.request, 'date_debut')
        date_fin = lire_date(self.request, 'date_fin')

        if auteur_id:
            qs = qs.filter(auteur_id=auteur_id)
        if action:
            qs = qs.filter(action__icontains=action)
        if entite:
            qs = qs.filter(entite_type__model=entite)
        if search:
            qs = qs.filter(Q(action__icontains=search) | Q(description__icontains=search))
        if date_debut:
            qs = qs.filter(date_heure__date__gte=date_debut)
        if date_fin:
            qs = qs.filter(date_heure__date__lte=date_fin)
        return qs
    
    
@extend_schema(parameters=[OpenApiParameter('q', OpenApiTypes.STR, description='Terme recherché (2 caractères min.)')],
               responses=OpenApiTypes.OBJECT, summary='Recherche globale (équipements, laboratoires)')
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def recherche_globale(request):
    terme = request.query_params.get('q', '').strip()
    if len(terme) < 2:
        return Response({'equipements': [], 'laboratoires': []})

    equipements = filtrer_par_organisation(Equipement.objects.all(), request.user, 'laboratoire__organisation')
    equipements = equipements.filter(nom__icontains=terme).select_related('laboratoire')[:5]
    laboratoires = filtrer_par_organisation(Laboratoire.objects.all(), request.user).filter(nom__icontains=terme)[:5]

    return Response({
        'equipements': [{'id': e.id, 'nom': e.nom, 'laboratoire_nom': e.laboratoire.nom} for e in equipements],
        'laboratoires': [{'id': l.id, 'nom': l.nom, 'localisation': l.localisation} for l in laboratoires],
    })
    
@extend_schema(responses=JournalActiviteSerializer(many=True), summary='Mes 10 dernières actions')
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def mon_activite(request):
    entrees = JournalActivite.objects.filter(auteur=request.user).select_related('auteur', 'entite_type').order_by('-date_heure')[:10]
    return Response(JournalActiviteSerializer(entrees, many=True).data)


# Un tableur interprète comme une formule toute cellule commençant par
# l'un de ces caractères. Un utilisateur qui mettrait « =HYPERLINK(...) »
# dans son nom ferait exécuter cette formule chez l'admin qui ouvre le
# rapport (injection de formule / CSV injection).
CARACTERES_FORMULE = ('=', '+', '-', '@', '\t', '\r')


def _cellule_sure(valeur):
    # L'apostrophe initiale force le tableur à traiter la cellule comme du
    # texte. Les nombres ne sont pas touchés (ils restent calculables).
    if isinstance(valeur, str) and valeur.startswith(CARACTERES_FORMULE):
        return "'" + valeur
    return valeur


def _ajouter_ligne(ws, valeurs):
    ws.append([_cellule_sure(v) for v in valeurs])


def _duree_heures(reservation):
    debut = datetime.combine(reservation.date, reservation.heure_debut)
    fin = datetime.combine(reservation.date, reservation.heure_fin)
    return (fin - debut).total_seconds() / 3600


@extend_schema(parameters=PARAMETRES_PERIODE, responses={(200, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'): OpenApiTypes.BINARY},
               summary="Rapport d'activité Excel (admin)")
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def rapports_export_excel(request):
    # Alignée sur la restriction déjà en place côté frontend (route /rapports
    # réservée à l'admin) — à élargir ensemble si tu veux l'ouvrir aux
    # techniciens/chercheurs plus tard.
    if request.user.role != Role.ADMIN:
        return Response({'detail': "Seul un administrateur peut exporter ce rapport."}, status=403)

    date_debut = lire_date(request, 'date_debut')
    date_fin = lire_date(request, 'date_fin')
    laboratoire_id = lire_id(request, 'laboratoire')

    reservations = filtrer_par_organisation(Reservation.objects.all(), request.user, 'laboratoire__organisation')
    reservations = reservations.exclude(est_archivee=True)
    if date_debut:
        reservations = reservations.filter(date__gte=date_debut)
    if date_fin:
        reservations = reservations.filter(date__lte=date_fin)
    if laboratoire_id:
        reservations = reservations.filter(laboratoire_id=laboratoire_id)

    reservations = list(reservations.select_related('laboratoire', 'demandeur').prefetch_related('equipements__laboratoire'))

    wb = openpyxl.Workbook()
    entete_font = Font(bold=True, color="FFFFFF")
    entete_fill = PatternFill(start_color="1B2CC1", end_color="1B2CC1", fill_type="solid")

    def style_entete(ws):
        for cell in ws[ws.max_row]:
            cell.font = entete_font
            cell.fill = entete_fill

    # --- Résumé ---
    ws = wb.active
    ws.title = "Résumé"
    _ajouter_ligne(ws, ["Rapport d'activité — SenLab"])
    ws['A1'].font = Font(bold=True, size=14)
    _ajouter_ligne(ws, [f"Période : {date_debut or '—'} au {date_fin or '—'}"])
    if laboratoire_id:
        # Filtré par établissement : un id d'un autre établissement ne doit pas révéler son nom.
        labo = filtrer_par_organisation(Laboratoire.objects.all(), request.user).filter(id=laboratoire_id).first()
        _ajouter_ligne(ws, [f"Laboratoire : {labo.nom if labo else '—'}"])
    _ajouter_ligne(ws, [])
    _ajouter_ligne(ws, ["Indicateur", "Valeur"])
    style_entete(ws)

    equipements_utilises = {e.id for r in reservations for e in r.equipements.all()}
    _ajouter_ligne(ws, ["Réservations sur la période", len(reservations)])
    _ajouter_ligne(ws, ["Équipements utilisés", len(equipements_utilises)])
    _ajouter_ligne(ws, ["Heures d'utilisation cumulées", round(sum(_duree_heures(r) for r in reservations), 1)])
    ws.column_dimensions['A'].width = 32
    ws.column_dimensions['B'].width = 18

    # --- Par laboratoire ---
    ws2 = wb.create_sheet("Par laboratoire")
    _ajouter_ligne(ws2, ["Laboratoire", "Réservations", "Heures cumulées"])
    style_entete(ws2)
    par_labo = {}
    for r in reservations:
        d = par_labo.setdefault(r.laboratoire.nom, {'n': 0, 'h': 0})
        d['n'] += 1
        d['h'] += _duree_heures(r)
    for nom, d in sorted(par_labo.items(), key=lambda x: -x[1]['n']):
        _ajouter_ligne(ws2, [nom, d['n'], round(d['h'], 1)])
    ws2.column_dimensions['A'].width = 34

    # --- Par équipement ---
    ws3 = wb.create_sheet("Par équipement")
    _ajouter_ligne(ws3, ["Équipement", "Laboratoire", "Réservations", "Heures cumulées"])
    style_entete(ws3)
    par_equip = {}
    for r in reservations:
        for e in r.equipements.all():
            d = par_equip.setdefault((e.nom, e.laboratoire.nom), {'n': 0, 'h': 0})
            d['n'] += 1
            d['h'] += _duree_heures(r)
    for (nom, labo_nom), d in sorted(par_equip.items(), key=lambda x: -x[1]['n']):
        _ajouter_ligne(ws3, [nom, labo_nom, d['n'], round(d['h'], 1)])
    ws3.column_dimensions['A'].width = 30
    ws3.column_dimensions['B'].width = 30

    # --- Par type d'utilisateur ---
    ws4 = wb.create_sheet("Par type d'utilisateur")
    _ajouter_ligne(ws4, ["Rôle", "Réservations"])
    style_entete(ws4)
    par_role = {}
    for r in reservations:
        label = ROLE_LABELS.get(r.demandeur.role, r.demandeur.role)
        par_role[label] = par_role.get(label, 0) + 1
    for label, n in sorted(par_role.items(), key=lambda x: -x[1]):
        _ajouter_ligne(ws4, [label, n])
    ws4.column_dimensions['A'].width = 26

    # --- Détail ---
    ws5 = wb.create_sheet("Détail des réservations")
    _ajouter_ligne(ws5, ["Date", "Début", "Fin", "Laboratoire", "Équipement(s)", "Demandeur", "Rôle", "Statut"])
    style_entete(ws5)
    for r in sorted(reservations, key=lambda x: x.date):
        equip = ", ".join(e.nom for e in r.equipements.all()) or "Salle uniquement"
        _ajouter_ligne(ws5, [
            r.date.strftime('%d/%m/%Y'), str(r.heure_debut)[:5], str(r.heure_fin)[:5],
            r.laboratoire.nom, equip, r.demandeur.nom_complet,
            ROLE_LABELS.get(r.demandeur.role, r.demandeur.role), r.get_statut_display(),
        ])
    for col, w in zip('ABCDEFGH', [12, 8, 8, 26, 30, 22, 18, 12]):
        ws5.column_dimensions[col].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="rapport_senlab_{datetime.now():%Y%m%d}.xlsx"'
    wb.save(response)
    return response

ROLES_PILOTAGE = [Role.ADMIN, Role.TECHNICIEN]


@extend_schema(parameters=PARAMETRES_PERIODE, responses=OpenApiTypes.OBJECT,
               summary="Aide à la décision : indicateurs, prévision et recommandations")
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def indicateurs_pilotage(request):
    """
    Aide à la décision : occupation, heures de pointe, prévision de charge
    et recommandations motivées (voir apps.core.analytique).
    """
    if request.user.role not in ROLES_PILOTAGE:
        return Response({'detail': "Réservé aux administrateurs et techniciens."}, status=403)
    return Response(calculer_indicateurs(
        request.user, lire_date(request, 'date_debut'), lire_date(request, 'date_fin'), lire_id(request, 'laboratoire'),
    ))


@extend_schema(parameters=PARAMETRES_PERIODE, responses={(200, 'application/pdf'): OpenApiTypes.BINARY},
               summary="Rapport d'activité PDF (admin)")
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def rapports_export_pdf(request):
    if request.user.role != Role.ADMIN:
        return Response({'detail': "Seul un administrateur peut exporter ce rapport."}, status=403)

    date_debut = lire_date(request, 'date_debut')
    date_fin = lire_date(request, 'date_fin')
    laboratoire_id = lire_id(request, 'laboratoire')
    indicateurs = calculer_indicateurs(request.user, date_debut, date_fin, laboratoire_id)

    reservations = filtrer_par_organisation(Reservation.objects.all(), request.user, 'laboratoire__organisation').filter(
        date__gte=indicateurs['periode']['debut'], date__lte=indicateurs['periode']['fin'],
    ).exclude(est_archivee=True)
    laboratoire_nom = None
    if laboratoire_id:
        reservations = reservations.filter(laboratoire_id=laboratoire_id)
        labo = filtrer_par_organisation(Laboratoire.objects.all(), request.user).filter(id=laboratoire_id).first()
        laboratoire_nom = labo.nom if labo else None
    reservations = list(reservations.select_related('laboratoire', 'demandeur').prefetch_related('equipements')
                        .order_by('date', 'heure_debut'))

    contenu = generer_rapport_pdf(indicateurs, reservations, request.user.organisation, laboratoire_nom)
    response = HttpResponse(contenu, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="rapport_activite_{datetime.now():%Y%m%d}.pdf"'
    return response


# --- Tâches planifiées déclenchées par l'automatisation (n8n) ---
# Sur un hébergement sans cron (ex. Render gratuit), le workflow n8n appelle
# ces URL à intervalle régulier. Elles ne sont pas liées à un utilisateur :
# elles exigent l'en-tête « X-Taches-Token » égal au secret TACHES_TOKEN.
# Sans secret configuré, elles sont désactivées (403).

def _jeton_taches_valide(request):
    import hmac
    from django.conf import settings
    attendu = settings.TACHES_TOKEN
    recu = request.headers.get('X-Taches-Token', '')
    # compare_digest : comparaison en temps constant (pas de fuite par le chronométrage).
    return bool(attendu) and hmac.compare_digest(recu, attendu)


@extend_schema(parameters=[OpenApiParameter('X-Taches-Token', OpenApiTypes.STR, OpenApiParameter.HEADER, required=True, description='Secret TACHES_TOKEN')], request=None, responses=OpenApiTypes.OBJECT, summary='Tâche planifiée : clôturer les réservations passées (n8n)')
@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def tache_cloturer_reservations(request):
    if not _jeton_taches_valide(request):
        return Response({'detail': 'Jeton de tâche invalide.'}, status=403)
    from apps.reservations.services import marquer_terminees
    terminees, expirees = marquer_terminees()
    return Response({'terminees': terminees, 'expirees': expirees})


@extend_schema(parameters=[OpenApiParameter('X-Taches-Token', OpenApiTypes.STR, OpenApiParameter.HEADER, required=True, description='Secret TACHES_TOKEN')], request=None, responses=OpenApiTypes.OBJECT, summary='Tâche planifiée : synthèse hebdomadaire aux admins (n8n)')
@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def tache_synthese_hebdomadaire(request):
    if not _jeton_taches_valide(request):
        return Response({'detail': 'Jeton de tâche invalide.'}, status=403)
    from apps.core.management.commands.envoyer_synthese_hebdomadaire import envoyer_syntheses
    return Response({'syntheses_envoyees': envoyer_syntheses()})
