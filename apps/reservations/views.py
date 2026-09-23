from datetime import datetime, timedelta

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from django.utils import timezone

from apps.core.services import enregistrer as journaliser
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

        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
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
        rang_projet = RANG_PRIORITE[projet.niveau_priorite] if projet else RANG_PRIORITE[NiveauPriorite.NORMALE]
        rang_academique = RANG_STATUT_ACADEMIQUE.get(utilisateur.statut_academique, 0)
        return (rang_projet, rang_academique)

    def _reponse_conflit(self, demandeur, projet, equipements, data, conflits):
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
        duree = datetime.combine(date, heure_fin) - datetime.combine(date, heure_debut)
        resultats = {'memes_equipements': [], 'equipements_equivalents': []}

        for equipement in equipements:
            for offset in range(6):
                jour = date + timedelta(days=offset)
                for debut, fin in creneaux_libres_jour(equipement.id, jour):
                    debut_dt, fin_dt = datetime.combine(jour, debut), datetime.combine(jour, fin)
                    if fin_dt - debut_dt >= duree:
                        resultats['memes_equipements'].append({
                            'equipement': equipement.nom, 'date': jour.isoformat(),
                            'heure_debut': debut.strftime('%H:%M'),
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
        equipement_id = request.query_params.get('equipement')
        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')

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
        reservation.valider(validateur=request.user)
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
        reservation.refuser(validateur=request.user)
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

    @action(detail=False, methods=['get'])
    def a_rappeler_24h(self, request):
        maintenant = timezone.localtime()
        debut_fenetre = maintenant + timedelta(hours=23)
        fin_fenetre = maintenant + timedelta(hours=25)

        candidates = Reservation.objects.filter(
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

    @action(detail=False, methods=['get'])
    def a_rappeler_1h(self, request):
        maintenant = timezone.localtime()
        debut_fenetre = maintenant
        fin_fenetre = maintenant + timedelta(hours=2)

        candidates = Reservation.objects.filter(
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