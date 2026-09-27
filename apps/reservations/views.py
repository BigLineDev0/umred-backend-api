from datetime import datetime, timedelta

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from django.utils import timezone

from apps.core.services import enregistrer as journaliser
from apps.core.utils import lire_date, lire_id
from apps.notifications.services import notifier, notifier_par_email
from apps.notifications.models import TypeNotification
from apps.utilisateurs.models import Utilisateur, StatutCompte, Role, RANG_STATUT_ACADEMIQUE
from apps.equipements.models import Equipement
from apps.projets.models import RANG_PRIORITE, NiveauPriorite

from .models import Reservation, StatutReservation
from .serializers import ReservationSerializer
from .services import creneaux_libres_jour


class EstValidateur(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [
            Role.TECHNICIEN, Role.CHERCHEUR, Role.ADMIN
        ]


class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == Role.ADMIN


class ReservationViewSet(viewsets.ModelViewSet):
    queryset = Reservation.objects.all()
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]
    # Pas de PUT/PATCH : une réservation ne se modifie pas après coup, car
    # une modification contournerait les contrôles de Reservation.creer()
    # (conflits, disponibilité, statut initial). Pour changer de créneau,
    # on annule et on refait une demande.
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_permissions(self):
        if self.action == 'destroy':
            return [EstAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        est_superviseur = user.role in [Role.ADMIN, Role.TECHNICIEN, Role.CHERCHEUR]
        laboratoire_id = lire_id(self.request, 'laboratoire')
        date_debut = lire_date(self.request, 'date_debut')
        date_fin = lire_date(self.request, 'date_fin')
        statut = self.request.query_params.get('statut')
        inclure_toutes = self.request.query_params.get('all') == 'true'

        # Règles de visibilité en liste :
        #  - ?all=true (superviseurs uniquement) : toutes les réservations ;
        #  - ?laboratoire=X : le planning du labo, limité aux réservations
        #    VALIDEES (ce qui occupe réellement la salle) ;
        #  - sinon : uniquement « mes » réservations.
        # Hors liste (retrieve, actions detail=True), un superviseur accède
        # à tout, un étudiant seulement à ses propres réservations : un id
        # appartenant à quelqu'un d'autre renvoie 404.
        if self.action == 'list':
            if inclure_toutes and est_superviseur:
                qs = Reservation.objects.all()
            elif laboratoire_id:
                qs = Reservation.objects.filter(statut=StatutReservation.VALIDEE)
            else:
                qs = Reservation.objects.filter(demandeur=user)

            if self.request.query_params.get('archivees') != 'true':
                qs = qs.exclude(est_archivee=True)

            if self.request.query_params.get('a_venir') == 'true':
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

        # Charge en une fois les objets liés affichés par le serializer
        # (évite une requête SQL par réservation : problème « N+1 »).
        return qs.select_related('demandeur', 'laboratoire', 'projet').prefetch_related('equipements')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Le serializer renvoie des objets Equipement ; on garde les objets
        # (pour les alternatives) et on en extrait les ids (pour le modèle).
        equipements = data.pop('equipements', [])
        equipements_ids = [e.id for e in equipements]
        projet = data.get('projet')

        # --- Détection de conflit AVANT création, pour pouvoir répondre
        # avec des alternatives plutôt qu'une simple erreur si un conflit
        # existe déjà sur l'un des équipements demandés.
        conflits = Reservation.objects.none()
        if equipements_ids:
            conflits = Reservation.objects.filter(
                equipements__id__in=equipements_ids, date=data['date'],
                statut__in=[StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE],
                heure_debut__lt=data['heure_fin'], heure_fin__gt=data['heure_debut'],
            ).distinct()

        if conflits.exists():
            return self._reponse_conflit(request.user, projet, equipements, data, conflits)

        reservation = Reservation(
            demandeur=request.user,
            laboratoire=data['laboratoire'],
            date=data['date'],
            heure_debut=data['heure_debut'],
            heure_fin=data['heure_fin'],
            motif=data['motif'],
            projet=projet,
        )

        try:
            reservation.creer(equipements_ids=equipements_ids)
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)

        journaliser(request.user, 'Création de réservation', reservation,
                    f'Statut initial : {reservation.statut}')

        # EN_ATTENTE -> on prévient ceux qui peuvent valider (notification
        # interne). VALIDEE d'office -> on confirme directement au
        # demandeur par email (via le webhook n8n).
        if reservation.statut == StatutReservation.EN_ATTENTE:
            # Mêmes rôles que la permission EstValidateur, sauf le demandeur
            # lui-même (il ne peut pas statuer sur sa propre demande).
            validateurs = Utilisateur.objects.filter(
                role__in=[Role.TECHNICIEN, Role.CHERCHEUR, Role.ADMIN], statut_compte=StatutCompte.ACTIF
            ).exclude(pk=request.user.pk)
            for validateur in validateurs:
                notifier(validateur, 'Nouvelle demande de réservation',
                         f'{request.user} a soumis une demande pour le {reservation.date}.',
                         TypeNotification.RESERVATION, reservation)
        else:
            notifier_par_email(reservation.demandeur, "confirmation", {
                "laboratoire": reservation.laboratoire.nom,
                "date": str(reservation.date),
                "heure_debut": str(reservation.heure_debut),
                "heure_fin": str(reservation.heure_fin),
            })

        return Response(self.get_serializer(reservation).data, status=status.HTTP_201_CREATED)

    # --- Résolution intelligente des conflits — méthodes privées ---

    def _cle_priorite(self, projet, utilisateur):
        """
        Tuple (priorité du projet, rang académique du demandeur). La
        comparaison de tuples en Python départage d'abord sur le premier
        élément, puis sur le second en cas d'égalité — exactement la règle
        voulue : le niveau du projet prime, le statut académique ne
        départage qu'à priorité de projet égale.
        """
        # Sans projet rattaché, la demande est traitée comme NORMALE ; sans
        # statut académique (étudiant, technicien...), le rang vaut 0.
        rang_projet = RANG_PRIORITE[projet.niveau_priorite] if projet else RANG_PRIORITE[NiveauPriorite.NORMALE]
        rang_academique = RANG_STATUT_ACADEMIQUE.get(utilisateur.statut_academique, 0)
        return (rang_projet, rang_academique)

    def _reponse_conflit(self, demandeur, projet, equipements, data, conflits):
        # Le système ne déplace JAMAIS une réservation automatiquement : il
        # répond 409 avec des créneaux alternatifs, et si le nouveau
        # demandeur est plus prioritaire qu'une demande encore en attente,
        # il alerte les techniciens/admins qui arbitrent humainement.
        ma_priorite = self._cle_priorite(projet, demandeur)
        priorite_superieure = False

        # On ne compare et n'alerte QUE sur les réservations encore
        # EN_ATTENTE — une réservation déjà VALIDEE n'est jamais remise en
        # question automatiquement, peu importe la priorité du demandeur.
        for c in conflits.filter(statut=StatutReservation.EN_ATTENTE):
            if ma_priorite > self._cle_priorite(c.projet, c.demandeur):
                priorite_superieure = True
                validateurs = Utilisateur.objects.filter(
                    role__in=[Role.TECHNICIEN, Role.ADMIN], statut_compte=StatutCompte.ACTIF
                )
                for v in validateurs:
                    notifier(v, 'Conflit de priorité détecté',
                             f"La demande de {demandeur.nom_complet} entre en conflit avec une demande en "
                             f"attente de {c.demandeur.nom_complet} sur le créneau du {data['date']}.",
                             TypeNotification.RESERVATION, c)

        return Response({
            'conflit': True,
            'detail': "Ce créneau est déjà réservé sur l'équipement demandé.",
            'priorite_superieure': priorite_superieure,
            'alternatives': self._chercher_alternatives(
                data['laboratoire'], equipements, data['date'], data['heure_debut'], data['heure_fin']
            ),
        }, status=status.HTTP_409_CONFLICT)

    def _chercher_alternatives(self, laboratoire, equipements, date, heure_debut, heure_fin):
        """
        Deux types de suggestions :
         1. même équipement, autre moment : pour chaque équipement, on
            parcourt le jour demandé + les 5 suivants et on retient la
            première plage libre assez longue pour la durée voulue (une
            seule proposition par jour grâce au break) ;
         2. même moment, autre équipement : un équipement de la même
            catégorie, dans le même labo, libre sur le créneau demandé.
        """
        # datetime.combine est nécessaire car on ne peut pas soustraire deux
        # objets time en Python ; on obtient ainsi un timedelta.
        duree =datetime.combine(date, heure_fin) - datetime.combine(date, heure_debut)
        resultats = {'memes_equipements': [], 'equipements_equivalents': []}

        # On ne propose jamais un créneau déjà commencé.
        maintenant = timezone.localtime().replace(tzinfo=None, second=0, microsecond=0)

        for equipement in equipements:
            for offset in range(6):
                jour = date + timedelta(days=offset)
                for debut, fin in creneaux_libres_jour(equipement.id, jour):
                    debut_dt, fin_dt = datetime.combine(jour, debut), datetime.combine(jour, fin)
                    debut_dt = max(debut_dt, maintenant)
                    if fin_dt - debut_dt >= duree:
                        resultats['memes_equipements'].append({
                            'equipement': equipement.nom, 'date': jour.isoformat(),
                            'heure_debut': debut_dt.strftime('%H:%M'),
                            'heure_fin': (debut_dt + duree).time().strftime('%H:%M'),
                        })
                        break
                if len(resultats['memes_equipements']) >= 3:
                    break

        for equipement in equipements:
            if not equipement.categorie:
                continue
            for equiv in Equipement.objects.filter(laboratoire=laboratoire, categorie=equipement.categorie).exclude(pk=equipement.pk):
                en_conflit = Reservation.objects.filter(
                    equipements=equiv, date=date, statut__in=[StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE],
                    heure_debut__lt=heure_fin, heure_fin__gt=heure_debut,
                ).exists()
                if not en_conflit:
                    resultats['equipements_equivalents'].append({'id': equiv.id, 'nom': equiv.nom})

        return resultats

    # --- Actions existantes, strictement inchangées ---

    @action(detail=False, methods=['get'])
    def creneaux_occupes(self, request):
        date_debut = lire_date(request, 'date_debut')
        date_fin = lire_date(request, 'date_fin')
        equipement_id = lire_id(request, 'equipement')

        qs = Reservation.objects.filter(statut__in=[StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE])
        if equipement_id:
            qs = qs.filter(equipements__id=equipement_id)
        if date_debut:
            qs = qs.filter(date__gte=date_debut)
        if date_fin:
            qs = qs.filter(date__lte=date_fin)

        return Response(list(qs.values('date', 'heure_debut', 'heure_fin')))

    @action(detail=True, methods=['post'], permission_classes=[EstValidateur])
    def valider(self, request, pk=None):
        reservation = self.get_object()
        try:
            reservation.valider(validateur=request.user)
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        journaliser(request.user, 'Validation de réservation', reservation)
        notifier(reservation.demandeur, 'Réservation validée',
                 f'Votre réservation du {reservation.date} a été validée.',
                 TypeNotification.VALIDATION, reservation)
        notifier_par_email(reservation.demandeur, "validation", {
            "laboratoire": reservation.laboratoire.nom, "date": str(reservation.date),
        })
        return Response(self.get_serializer(reservation).data)

    @action(detail=True, methods=['post'], permission_classes=[EstValidateur])
    def refuser(self, request, pk=None):
        reservation = self.get_object()
        try:
            reservation.refuser(validateur=request.user)
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
        journaliser(request.user, 'Refus de réservation', reservation)
        notifier(reservation.demandeur, 'Réservation refusée',
                 f'Votre réservation du {reservation.date} a été refusée.',
                 TypeNotification.VALIDATION, reservation)
        notifier_par_email(reservation.demandeur, "refus", {
            "laboratoire": reservation.laboratoire.nom, "date": str(reservation.date),
        })
        return Response(self.get_serializer(reservation).data)

    @action(detail=True, methods=['post'])
    def annuler(self, request, pk=None):
        reservation = self.get_object()
        try:
            reservation.annuler()
        except DjangoValidationError as e:
            raise DRFValidationError(e.messages)
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

    # --- Rappels (consommés par le workflow d'automatisation) ---
    # Le workflow interroge ces endpoints périodiquement, envoie les
    # rappels, puis appelle marquer_rappel_*_envoye pour ne jamais envoyer
    # deux fois le même rappel. La fenêtre de tolérance (23h-25h, 0-2h)
    # évite de rater une réservation si le workflow tourne en léger décalage.
    # Filtrage en deux temps : le SQL pré-filtre par date (date et heure
    # sont deux colonnes séparées), puis Python compare le datetime exact.

    # Réservés à l'admin (compte de service du workflow), comme les actions
    # marquer_rappel_* : ils listent les réservations de TOUS les
    # utilisateurs, avec leurs emails.
    @action(detail=False, methods=['get'], permission_classes=[EstAdmin])
    def a_rappeler_24h(self, request):
        maintenant = timezone.localtime()
        debut_fenetre = maintenant + timedelta(hours=23)
        fin_fenetre = maintenant + timedelta(hours=25)

        candidates = Reservation.objects.select_related('demandeur', 'laboratoire', 'projet').prefetch_related('equipements').filter(
            statut=StatutReservation.VALIDEE,
            rappel_24h_envoye=False,
            date__gte=debut_fenetre.date(),
            date__lte=fin_fenetre.date(),
        )
        resultats = [
            r for r in candidates
            if debut_fenetre <= timezone.make_aware(datetime.combine(r.date, r.heure_debut)) <= fin_fenetre
        ]
        return Response(self.get_serializer(resultats, many=True).data)

    @action(detail=False, methods=['get'], permission_classes=[EstAdmin])
    def a_rappeler_1h(self, request):
        maintenant = timezone.localtime()
        debut_fenetre = maintenant
        fin_fenetre = maintenant + timedelta(hours=2)

        candidates = Reservation.objects.select_related('demandeur', 'laboratoire', 'projet').prefetch_related('equipements').filter(
            statut=StatutReservation.VALIDEE,
            rappel_1h_envoye=False,  # corrigé — pointait sur rappel_24h_envoye par erreur
            date__gte=debut_fenetre.date(),
            date__lte=fin_fenetre.date(),
        )
        resultats = [
            r for r in candidates
            if debut_fenetre <= timezone.make_aware(datetime.combine(r.date, r.heure_debut)) <= fin_fenetre
        ]
        return Response(self.get_serializer(resultats, many=True).data)

    @action(detail=True, methods=['post'], permission_classes=[EstAdmin])
    def marquer_rappel_24h_envoye(self, request, pk=None):
        reservation = self.get_object()
        reservation.rappel_24h_envoye = True
        reservation.save(update_fields=['rappel_24h_envoye'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], permission_classes=[EstAdmin])
    def marquer_rappel_1h_envoye(self, request, pk=None):
        reservation = self.get_object()
        reservation.rappel_1h_envoye = True
        reservation.save(update_fields=['rappel_1h_envoye'])
        return Response(status=status.HTTP_204_NO_CONTENT)