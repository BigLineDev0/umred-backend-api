from datetime import datetime, timedelta

from rest_framework import viewsets, mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from django.utils import timezone

from apps.core.services import enregistrer as journaliser
from apps.core.utils import lire_date, lire_id
from apps.notifications.services import notifier, notifier_par_email
from apps.notifications.models import TypeNotification
from apps.organisations.isolation import est_super_admin, filtrer_par_organisation
from apps.projets.models import NiveauPriorite
from apps.utilisateurs.models import Role, StatutAcademique

from .models import AlerteCreneau, Reservation, StatutReservation, STATUTS_BLOQUANTS, STATUTS_EQUIPEMENT_NON_RESERVABLES
from .serializers import AlerteCreneauSerializer, RefusSerializer, ReservationSerializer
from .services import (
    analyser_file, conflits_detailles, creer_alerte, equipements_equivalents, liberer_creneau,
    proposer_creneaux, validateurs_pour,
)

ROLES_SUPERVISEURS = [Role.ADMIN, Role.TECHNICIEN, Role.CHERCHEUR]


class EstValidateur(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ROLES_SUPERVISEURS


class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == Role.ADMIN


class EstAdminOuService(permissions.BasePermission):
    """
    Rappels : l'admin d'un établissement (limité à ses réservations) ou le
    compte de service du workflow d'automatisation, qui a le rôle
    SUPER_ADMIN et traite les rappels de tous les établissements.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and (request.user.role == Role.ADMIN or est_super_admin(request.user))


class ReservationViewSet(viewsets.ModelViewSet):
    queryset = Reservation.objects.all()
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]
    # Pas de PUT/PATCH : une réservation ne se modifie pas après coup, car
    # une modification contournerait les contrôles de Reservation.creer()
    # (conflits, disponibilité, statut initial). Pour changer de créneau,
    # on annule et on refait une demande.
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    ACTIONS_RAPPEL = ['a_rappeler_24h', 'a_rappeler_1h', 'marquer_rappel_24h_envoye', 'marquer_rappel_1h_envoye']

    def get_permissions(self):
        if self.action == 'destroy':
            return [EstAdmin()]
        return super().get_permissions()

    def _base(self):
        """Réservations de l'établissement de l'utilisateur (isolation SaaS)."""
        user = self.request.user
        if self.action in self.ACTIONS_RAPPEL and est_super_admin(user):
            return Reservation.objects.all()
        return filtrer_par_organisation(Reservation.objects.all(), user, 'laboratoire__organisation')

    def get_queryset(self):
        user = self.request.user
        est_superviseur = user.role in ROLES_SUPERVISEURS
        laboratoire_id = lire_id(self.request, 'laboratoire')
        date_debut = lire_date(self.request, 'date_debut')
        date_fin = lire_date(self.request, 'date_fin')
        statut = self.request.query_params.get('statut')
        inclure_toutes = self.request.query_params.get('all') == 'true'
        base = self._base()

        # Règles de visibilité en liste :
        #  - ?all=true (superviseurs uniquement) : toutes les réservations ;
        #  - ?laboratoire=X : le planning du labo, limité aux réservations
        #    VALIDEES (ce qui occupe réellement la salle) ;
        #  - sinon : uniquement « mes » réservations.
        # Hors liste (retrieve, actions detail=True), un superviseur accède
        # à tout l'établissement, un étudiant seulement à ses propres
        # réservations : un id appartenant à quelqu'un d'autre renvoie 404.
        if self.action == 'list':
            if inclure_toutes and est_superviseur:
                qs = base
            elif laboratoire_id:
                qs = base.filter(statut=StatutReservation.VALIDEE)
            else:
                qs = base.filter(demandeur=user)

            if self.request.query_params.get('archivees') != 'true':
                qs = qs.exclude(est_archivee=True)

            if self.request.query_params.get('a_venir') == 'true':
                qs = qs.filter(date__gte=timezone.localdate())
        elif self.action in self.ACTIONS_RAPPEL:
            qs = base
        else:
            qs = base if est_superviseur else base.filter(demandeur=user)

        if laboratoire_id:
            qs = qs.filter(laboratoire_id=laboratoire_id)
        if date_debut:
            qs = qs.filter(date__gte=date_debut)
        if date_fin:
            qs = qs.filter(date__lte=date_fin)
        if statut:
            qs = qs.filter(statut=statut)

        # Charge en une fois les objets liés affichés par le serializer
        # (évite une requête SQL par réservation : problème « N+1 »).
        return qs.select_related('demandeur', 'laboratoire__organisation', 'projet').prefetch_related('equipements')

    # --- Construction et analyse d'une demande ---

    def _lire_demande(self, request):
        """Valide le JSON et renvoie (réservation non enregistrée, équipements)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Le serializer renvoie des objets Equipement : on garde les objets
        # (statut, catégorie) et on en extrait les ids (pour le modèle).
        equipements = data.pop('equipements', [])
        reservation = Reservation(
            demandeur=request.user,
            laboratoire=data['laboratoire'],
            date=data['date'],
            heure_debut=data['heure_debut'],
            heure_fin=data['heure_fin'],
            motif=data['motif'],
            projet=data.get('projet'),
        )
        try:
            reservation.preparer([e.id for e in equipements])
            Reservation._verifier_disponibilite(reservation.laboratoire, equipements)
            reservation._verifier_maintenance([e.id for e in equipements])
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        return reservation, equipements

    def _alternatives(self, reservation, equipements, conflits):
        ids = [e.id for e in equipements]
        return {
            'creneaux': proposer_creneaux(
                ids, reservation.date, reservation.heure_debut, reservation.heure_fin, reservation.regles,
            ),
            'equipements_equivalents': equipements_equivalents(
                reservation.laboratoire, equipements, conflits,
                reservation.date, reservation.heure_debut, reservation.heure_fin,
            ),
        }

    def _qui_traitera(self, reservation, equipements):
        sensible = any(e.necessite_validation for e in equipements)
        encadrant = reservation.demandeur.encadrant
        if encadrant and not sensible:
            return f"Votre encadrant, {encadrant.nom_complet}, traitera votre demande."
        return "Un technicien du laboratoire traitera votre demande."

    @action(detail=False, methods=['post'])
    def verifier(self, request):
        """
        Vérification AVANT confirmation : le frontend l'appelle en ouvrant
        le récapitulatif, pour afficher tout de suite les conflits, les
        créneaux alternatifs, le statut que prendra la demande et sa place
        dans la file d'attente. Rien n'est enregistré.
        """
        reservation, equipements = self._lire_demande(request)
        conflits = conflits_detailles(equipements, reservation.date, reservation.heure_debut, reservation.heure_fin)
        if conflits:
            return Response({
                'disponible': False,
                'conflits': conflits,
                'alternatives': self._alternatives(reservation, equipements, conflits),
            })

        ids = [e.id for e in equipements]
        file = analyser_file(reservation, ids)
        statut, raison = reservation.statut_initial(equipements, file['nombre'] > 0)
        if statut == StatutReservation.EN_ATTENTE:
            raison = f"{raison} {self._qui_traitera(reservation, equipements)}"
        return Response({
            'disponible': True,
            'conflits': [],
            'statut_prevu': statut,
            'raison_statut': raison,
            'file_attente': file,
            'duree_minutes': reservation.duree_minutes,
        })

    def create(self, request, *args, **kwargs):
        reservation, equipements = self._lire_demande(request)
        equipements_ids = [e.id for e in equipements]

        # Conflit avec une réservation acquise : on répond 409 avec des
        # alternatives plutôt qu'une simple erreur.
        conflits = conflits_detailles(equipements, reservation.date, reservation.heure_debut, reservation.heure_fin)
        if conflits:
            return Response({
                'conflit': True,
                'detail': "Ce créneau est déjà réservé sur l'équipement demandé.",
                'conflits': conflits,
                'alternatives': self._alternatives(reservation, equipements, conflits),
            }, status=status.HTTP_409_CONFLICT)

        try:
            reservation.creer(equipements_ids=equipements_ids)
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)

        journaliser(request.user, 'Création de réservation', reservation,
                    f'Statut initial : {reservation.statut}')

        # EN_ATTENTE -> on prévient ceux qui peuvent la traiter (l'encadrant
        # en priorité). VALIDEE d'office -> confirmation par email au
        # demandeur (via le webhook n8n).
        if reservation.statut == StatutReservation.EN_ATTENTE:
            for validateur in validateurs_pour(reservation):
                notifier(validateur, 'Nouvelle demande de réservation',
                         f'{request.user.nom_complet} a soumis une demande pour le {reservation.date:%d/%m/%Y} '
                         f'({reservation.heure_debut:%H:%M}-{reservation.heure_fin:%H:%M}).',
                         TypeNotification.RESERVATION, reservation)
        else:
            notifier_par_email(reservation.demandeur, "confirmation", {
                "laboratoire": reservation.laboratoire.nom,
                "date": str(reservation.date),
                "heure_debut": str(reservation.heure_debut),
                "heure_fin": str(reservation.heure_fin),
            })

        return Response(self.get_serializer(reservation).data, status=status.HTTP_201_CREATED)

    # --- File d'attente des validateurs ---

    @action(detail=False, methods=['get'], permission_classes=[EstValidateur])
    def file_attente(self, request):
        """
        Demandes que l'utilisateur peut traiter, avec pour chacune une
        analyse d'aide à la décision : concurrence sur le créneau, rang de
        priorité, obstacles éventuels et une recommandation motivée.
        Un enseignant-chercheur ne voit que les demandes de ses étudiants.
        """
        user = request.user
        qs = self._base().filter(statut=StatutReservation.EN_ATTENTE).select_related(
            'demandeur__encadrant', 'laboratoire__organisation', 'projet',
        ).prefetch_related('equipements').order_by('date', 'heure_debut', 'date_creation')
        if user.role == Role.CHERCHEUR:
            qs = qs.filter(demandeur__encadrant=user)

        resultats = []
        for reservation in qs:
            if not reservation.peut_statuer(user):
                continue
            donnees = self.get_serializer(reservation).data
            donnees['analyse'] = self._analyser_pour_validateur(reservation)
            resultats.append(donnees)
        return Response(resultats)

    def _analyser_pour_validateur(self, reservation):
        equipements = list(reservation.equipements.all())
        ids = [e.id for e in equipements]
        concurrentes = list(reservation.concurrentes(ids).select_related('projet', 'demandeur'))
        rang = reservation.rang_dans_la_file(concurrentes)
        projet = reservation.projet
        niveau = projet.get_niveau_priorite_display() if projet else NiveauPriorite.NORMALE.label
        statut_academique = reservation.demandeur.statut_academique
        profil = StatutAcademique(statut_academique).label if statut_academique else reservation.demandeur.get_role_display()
        creneau_passe = datetime.combine(reservation.date, reservation.heure_debut) < timezone.localtime().replace(tzinfo=None)
        conflit = Reservation._a_un_conflit(reservation.date, reservation.heure_debut, reservation.heure_fin, ids, reservation.pk)
        en_panne = [e.nom for e in equipements if e.statut in STATUTS_EQUIPEMENT_NON_RESERVABLES]

        if creneau_passe:
            recommandation, raison = 'refuser', "Le créneau est déjà passé."
        elif conflit:
            recommandation, raison = 'refuser', "Le créneau a déjà été attribué à une autre réservation."
        elif en_panne:
            recommandation, raison = 'refuser', f"Équipement indisponible (panne ou maintenance) : {', '.join(en_panne)}."
        elif concurrentes and rang > 1:
            recommandation, raison = 'arbitrer', (
                f"{len(concurrentes)} demande(s) concurrente(s) ; celle-ci est classée {rang}e selon les priorités."
            )
        elif concurrentes:
            recommandation, raison = 'valider', (
                f"Prioritaire parmi {len(concurrentes) + 1} demandes (projet {niveau.lower()}, {profil.lower()}). "
                "La valider refusera automatiquement les autres, avec des alternatives."
            )
        else:
            recommandation, raison = 'valider', "Créneau libre, aucune demande concurrente."

        return {
            'priorite_projet': niveau,
            'profil_demandeur': profil,
            'concurrentes': len(concurrentes),
            'rang': rang,
            'creneau_passe': creneau_passe,
            'conflit_avec_reservation_validee': conflit,
            'recommandation': recommandation,
            'raison': raison,
        }

    # --- Liste d'attente des créneaux ---

    @action(detail=False, methods=['get', 'post'])
    def alertes(self, request):
        """
        GET : mes alertes actives. POST : « prévenez-moi si ce créneau se
        libère » (proposé après un conflit).
        """
        if request.method == 'GET':
            alertes = AlerteCreneau.objects.filter(
                utilisateur=request.user, active=True, date__gte=timezone.localdate(),
            ).select_related('laboratoire').prefetch_related('equipements')
            return Response(AlerteCreneauSerializer(alertes, many=True).data)

        serializer = AlerteCreneauSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        alerte = creer_alerte(
            request.user, data['laboratoire'], [e.id for e in data.get('equipements', [])],
            data['date'], data['heure_debut'], data['heure_fin'],
        )
        return Response(AlerteCreneauSerializer(alerte).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path=r'alertes/(?P<alerte_id>\d+)/supprimer')
    def supprimer_alerte(self, request, alerte_id=None):
        supprimees, _ = AlerteCreneau.objects.filter(pk=alerte_id, utilisateur=request.user).delete()
        if not supprimees:
            return Response({'detail': 'Alerte introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # --- Actions ---

    @action(detail=False, methods=['get'])
    def creneaux_occupes(self, request):
        """
        Pour le calendrier : les créneaux acquis (qui bloquent) et les
        demandes en attente (qui ne bloquent pas mais signalent une
        concurrence probable), distingués par 'statut'.
        """
        date_debut = lire_date(request, 'date_debut')
        date_fin = lire_date(request, 'date_fin')
        equipement_id = lire_id(request, 'equipement')

        qs = self._base().filter(statut__in=STATUTS_BLOQUANTS + [StatutReservation.EN_ATTENTE])
        if equipement_id:
            qs = qs.filter(equipements__id=equipement_id)
        if date_debut:
            qs = qs.filter(date__gte=date_debut)
        if date_fin:
            qs = qs.filter(date__lte=date_fin)

        return Response(list(qs.values('date', 'heure_debut', 'heure_fin', 'statut').distinct()))

    @action(detail=True, methods=['post'], permission_classes=[EstValidateur])
    def valider(self, request, pk=None):
        reservation = self.get_object()
        try:
            refusees = reservation.valider(validateur=request.user)
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        journaliser(request.user, 'Validation de réservation', reservation,
                    f'{len(refusees)} demande(s) concurrente(s) refusée(s)' if refusees else '')
        notifier(reservation.demandeur, 'Réservation validée',
                 f'Votre réservation du {reservation.date:%d/%m/%Y} a été validée.',
                 TypeNotification.VALIDATION, reservation)
        notifier_par_email(reservation.demandeur, "validation", {
            "laboratoire": reservation.laboratoire.nom, "date": str(reservation.date),
        })
        for refusee in refusees:
            self._prevenir_refus_concurrence(refusee)
        return Response(self.get_serializer(reservation).data)

    def _prevenir_refus_concurrence(self, reservation):
        """
        Le demandeur écarté reçoit la meilleure alternative calculée, et il
        est inscrit d'office sur la liste d'attente du créneau : si la
        réservation retenue est annulée, il sera prévenu.
        """
        ids = list(reservation.equipements.values_list('id', flat=True))
        propositions = proposer_creneaux(
            ids, reservation.date, reservation.heure_debut, reservation.heure_fin, reservation.regles, limite=1,
        )
        suggestion = f" Suggestion : {propositions[0]['message'].lower()}." if propositions else ''
        notifier(reservation.demandeur, 'Réservation non retenue',
                 f"Votre demande du {reservation.date:%d/%m/%Y} ({reservation.heure_debut:%H:%M}-"
                 f"{reservation.heure_fin:%H:%M}) a été attribuée à une demande prioritaire.{suggestion} "
                 "Vous serez prévenu si le créneau se libère.",
                 TypeNotification.VALIDATION, reservation, email=True)
        creer_alerte(reservation.demandeur, reservation.laboratoire, ids,
                     reservation.date, reservation.heure_debut, reservation.heure_fin)
        journaliser(reservation.validateur, 'Refus automatique (concurrence)', reservation)

    @action(detail=True, methods=['post'], permission_classes=[EstValidateur])
    def refuser(self, request, pk=None):
        reservation = self.get_object()
        serializer = RefusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        motif = serializer.validated_data.get('motif', '')
        try:
            reservation.refuser(validateur=request.user, motif=motif)
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        journaliser(request.user, 'Refus de réservation', reservation, motif)
        notifier(reservation.demandeur, 'Réservation refusée',
                 f'Votre réservation du {reservation.date:%d/%m/%Y} a été refusée.' + (f' Motif : {motif}' if motif else ''),
                 TypeNotification.VALIDATION, reservation)
        notifier_par_email(reservation.demandeur, "refus", {
            "laboratoire": reservation.laboratoire.nom, "date": str(reservation.date),
        })
        return Response(self.get_serializer(reservation).data)

    @action(detail=True, methods=['post'])
    def annuler(self, request, pk=None):
        reservation = self.get_object()
        user = request.user
        # Le demandeur annule sa réservation ; un technicien ou un admin peut
        # annuler celle d'un autre (ex. fermeture exceptionnelle du labo).
        if reservation.demandeur_id != user.id and user.role not in [Role.TECHNICIEN, Role.ADMIN]:
            raise DRFValidationError("Vous ne pouvez annuler que vos propres réservations.")
        try:
            liberait_creneau = reservation.annuler()
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        journaliser(user, 'Annulation de réservation', reservation)
        if reservation.demandeur_id != user.id:
            notifier(reservation.demandeur, 'Réservation annulée',
                     f'Votre réservation du {reservation.date:%d/%m/%Y} a été annulée par {user.nom_complet}.',
                     TypeNotification.RESERVATION, reservation, email=True)
        if liberait_creneau:
            liberer_creneau(reservation)
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

    # --- Rappels (consommés par le workflow d'automatisation) ---
    # Le workflow interroge ces endpoints périodiquement, envoie les
    # rappels, puis appelle marquer_rappel_*_envoye pour ne jamais envoyer
    # deux fois le même rappel. La fenêtre de tolérance (23h-25h, 0-2h)
    # évite de rater une réservation si le workflow tourne en léger décalage.
    # Filtrage en deux temps : le SQL pré-filtre par date (date et heure
    # sont deux colonnes séparées), puis Python compare le datetime exact.

    def _a_rappeler(self, debut_fenetre, fin_fenetre, champ_envoye):
        candidates = self.get_queryset().filter(
            statut=StatutReservation.VALIDEE,
            date__gte=debut_fenetre.date(),
            date__lte=fin_fenetre.date(),
            **{champ_envoye: False},
        )
        resultats = [
            r for r in candidates
            if debut_fenetre <= timezone.make_aware(datetime.combine(r.date, r.heure_debut)) <= fin_fenetre
        ]
        return Response(self.get_serializer(resultats, many=True).data)

    @action(detail=False, methods=['get'], permission_classes=[EstAdminOuService])
    def a_rappeler_24h(self, request):
        maintenant = timezone.localtime()
        return self._a_rappeler(maintenant + timedelta(hours=23), maintenant + timedelta(hours=25), 'rappel_24h_envoye')

    @action(detail=False, methods=['get'], permission_classes=[EstAdminOuService])
    def a_rappeler_1h(self, request):
        maintenant = timezone.localtime()
        return self._a_rappeler(maintenant, maintenant + timedelta(hours=2), 'rappel_1h_envoye')

    @action(detail=True, methods=['post'], permission_classes=[EstAdminOuService])
    def marquer_rappel_24h_envoye(self, request, pk=None):
        reservation = self.get_object()
        reservation.rappel_24h_envoye = True
        reservation.save(update_fields=['rappel_24h_envoye'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], permission_classes=[EstAdminOuService])
    def marquer_rappel_1h_envoye(self, request, pk=None):
        reservation = self.get_object()
        reservation.rappel_1h_envoye = True
        reservation.save(update_fields=['rappel_1h_envoye'])
        return Response(status=status.HTTP_204_NO_CONTENT)
