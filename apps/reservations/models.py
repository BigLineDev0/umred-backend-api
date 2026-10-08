from datetime import datetime, timedelta

from django.conf import settings
from django.db import models, transaction
from django.core.exceptions import ValidationError
from apps.laboratoires.models import Laboratoire, StatutLaboratoire
from apps.equipements.models import Equipement, StatutEquipement
from apps.organisations.isolation import regles_reservation
from apps.projets.models import RANG_PRIORITE, NiveauPriorite
from django.utils import timezone

# Un équipement dans l'un de ces états ne peut pas être réservé : il est
# soit en panne, soit immobilisé par une maintenance, soit retiré du parc.
STATUTS_EQUIPEMENT_NON_RESERVABLES = [
    StatutEquipement.EN_PANNE,
    StatutEquipement.EN_MAINTENANCE,
    StatutEquipement.HORS_SERVICE,
]


class StatutReservation(models.TextChoices):
    EN_ATTENTE = 'EN_ATTENTE', 'En attente'
    VALIDEE = 'VALIDEE', 'Validée'
    REFUSEE = 'REFUSEE', 'Refusée'
    ANNULEE = 'ANNULEE', 'Annulée'
    TERMINEE = 'TERMINEE', 'Terminée'


# Seules les réservations acquises occupent réellement un créneau. Une
# demande EN_ATTENTE ne bloque personne : plusieurs demandes peuvent viser
# le même créneau, elles forment une file départagée par priorité au
# moment de la validation (voir Reservation.valider).
STATUTS_BLOQUANTS = [StatutReservation.VALIDEE, StatutReservation.TERMINEE]

MOTIF_REFUS_CONCURRENCE = "Créneau attribué à une demande prioritaire sur le même équipement."

# Seule une réservation dont l'issue est définitive s'archive : archiver une
# demande en attente la ferait disparaître de la file sans décision, et une
# réservation validée à venir bloquerait un créneau invisible dans les listes.
STATUTS_ARCHIVABLES = [StatutReservation.REFUSEE, StatutReservation.ANNULEE, StatutReservation.TERMINEE]


class Reservation(models.Model):
    demandeur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reservations_effectuees'
    )
    validateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reservations_validees'
    )
    laboratoire = models.ForeignKey(
        Laboratoire, on_delete=models.CASCADE, related_name='reservations'
    )
    equipements = models.ManyToManyField(
        Equipement, blank=True, related_name='reservations'
    )

    date = models.DateField()
    heure_debut = models.TimeField()
    heure_fin = models.TimeField()
    motif = models.TextField()
    statut = models.CharField(
        max_length=20, choices=StatutReservation.choices, default=StatutReservation.EN_ATTENTE
    )
    motif_refus = models.TextField(blank=True)
    projet = models.ForeignKey(
        'projets.Projet', on_delete=models.SET_NULL, null=True, blank=True, related_name='reservations'
    )
    est_archivee = models.BooleanField(default=False)
    # Traçabilité : chaque décision garde QUI et QUAND, en plus du journal
    # d'activité (validateur/date_validation pour validation et refus).
    archivee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reservations_archivees'
    )
    date_archivage = models.DateTimeField(null=True, blank=True)
    annulee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reservations_annulees'
    )
    date_annulation = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_validation = models.DateTimeField(null=True, blank=True)
    rappel_24h_envoye = models.BooleanField(default=False)
    rappel_1h_envoye = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Réservation'
        verbose_name_plural = 'Réservations'
        ordering = ['-date', '-heure_debut']

    def __str__(self):
        return f'{self.demandeur} — {self.laboratoire} — {self.date}'

    # --- Règles de l'établissement ---

    @property
    def regles(self):
        laboratoire = self.laboratoire if self.laboratoire_id else None
        return regles_reservation(laboratoire.organisation if laboratoire else None)

    @property
    def duree_minutes(self):
        debut = datetime.combine(self.date, self.heure_debut)
        fin = datetime.combine(self.date, self.heure_fin)
        return int((fin - debut).total_seconds() // 60)

    def clean(self):
        if self.heure_fin <= self.heure_debut:
            raise ValidationError("L'heure de fin doit être après l'heure de début.")
        # Une durée minimale évite les réservations absurdes (10h00-10h05)
        # qui morcellent le planning ; la maximale empêche un seul
        # utilisateur de monopoliser un équipement toute la journée.
        regles = self.regles
        if self.duree_minutes < regles.duree_min:
            raise ValidationError(f"Une réservation doit durer au moins {regles.duree_min} minutes.")
        if self.duree_minutes > regles.duree_max:
            raise ValidationError(f"Une réservation ne peut pas dépasser {regles.duree_max // 60}h{regles.duree_max % 60:02d}.")

    def _verifier_creneau(self):
        # Pas de réservation dans le passé (ni plus tôt aujourd'hui),
        # uniquement pendant les heures d'ouverture, et pas trop longtemps
        # à l'avance (le planning lointain n'est pas encore stable).
        regles = self.regles
        maintenant = timezone.localtime()
        if datetime.combine(self.date, self.heure_debut) < maintenant.replace(tzinfo=None):
            raise ValidationError("Impossible de réserver un créneau déjà passé.")
        # Horaires du jour de la semaine concerné (un jour peut être fermé).
        ferme, ouverture, fermeture = regles.jour(self.date.weekday())
        if ferme:
            raise ValidationError("L'établissement est fermé ce jour-là : aucune réservation possible.")
        if self.heure_debut < ouverture or self.heure_fin > fermeture:
            raise ValidationError(
                f"Ce jour-là, les réservations sont possibles entre {ouverture:%H:%M} et {fermeture:%H:%M}."
            )
        if self.date > maintenant.date() + timedelta(days=regles.delai_max_jours):
            raise ValidationError(f"On ne peut pas réserver plus de {regles.delai_max_jours} jours à l'avance.")

    @staticmethod
    def _verifier_disponibilite(laboratoire, equipements):
        if laboratoire.statut == StatutLaboratoire.INDISPONIBLE:
            raise ValidationError("Ce laboratoire est actuellement indisponible.")
        indisponibles = [e.nom for e in equipements if e.statut in STATUTS_EQUIPEMENT_NON_RESERVABLES]
        if indisponibles:
            raise ValidationError(
                f"Équipement(s) non réservable(s) (panne, maintenance ou hors service) : {', '.join(indisponibles)}."
            )

    def _verifier_maintenance(self, equipements_ids):
        from apps.maintenances.models import Maintenance
        prevue = Maintenance.jour_bloque(equipements_ids, self.date).first()
        if prevue:
            raise ValidationError(
                f"Une maintenance est prévue sur {prevue.equipement.nom} le {self.date:%d/%m/%Y} : choisissez un autre jour."
            )

    @staticmethod
    def _verifier_equipements(laboratoire, equipements_ids):
        # On compte les équipements qui correspondent à la fois aux ids
        # demandés ET au laboratoire choisi. Si le compte diffère du nombre
        # d'ids distincts (set() retire les doublons), c'est qu'au moins un
        # équipement appartient à un autre labo (ou n'existe pas).
        if not equipements_ids:
            return
        trouves = Equipement.objects.filter(
            id__in=equipements_ids, laboratoire=laboratoire
        ).count()
        if trouves != len(set(equipements_ids)):
            raise ValidationError(
                "Un ou plusieurs équipements sélectionnés n'appartiennent pas à ce laboratoire."
            )

    # --- Chevauchements ---

    @classmethod
    def chevauchements(cls, date, heure_debut, heure_fin, equipements_ids, statuts, exclure_pk=None):
        """
        Réservations qui partagent au moins un équipement ET dont le
        créneau chevauche celui demandé. Deux créneaux [A_debut, A_fin[ et
        [B_debut, B_fin[ se chevauchent si et seulement si A_debut < B_fin
        ET A_fin > B_debut. Les inégalités strictes permettent d'enchaîner
        deux créneaux bord à bord (10h-12h puis 12h-14h).
        """
        if not equipements_ids:
            return cls.objects.none()
        qs = cls.objects.filter(
            equipements__id__in=equipements_ids,
            date=date,
            statut__in=statuts,
            heure_debut__lt=heure_fin,
            heure_fin__gt=heure_debut,
        ).distinct()
        if exclure_pk:
            qs = qs.exclude(pk=exclure_pk)
        return qs

    @classmethod
    def _a_un_conflit(cls, date, heure_debut, heure_fin, equipements_ids, exclure_pk=None):
        # Il suffit d'UN équipement déjà acquis sur le créneau pour refuser
        # la réservation entière (pas de réservation partielle).
        return cls.chevauchements(
            date, heure_debut, heure_fin, equipements_ids, STATUTS_BLOQUANTS, exclure_pk
        ).exists()

    def concurrentes(self, equipements_ids=None):
        """Autres demandes EN_ATTENTE qui visent le même créneau sur le même équipement."""
        if equipements_ids is None:
            equipements_ids = list(self.equipements.values_list('id', flat=True))
        return Reservation.chevauchements(
            self.date, self.heure_debut, self.heure_fin, equipements_ids,
            [StatutReservation.EN_ATTENTE], exclure_pk=self.pk,
        )

    # --- Priorité ---

    def cle_priorite(self):
        """
        Tuple (priorité du projet, rang académique du demandeur). Python
        compare les tuples élément par élément : le niveau du projet prime,
        le statut académique ne départage qu'à priorité de projet égale.
        Sans projet, la demande est NORMALE ; sans statut académique, rang 0.
        """
        from apps.utilisateurs.models import RANG_STATUT_ACADEMIQUE

        rang_projet = RANG_PRIORITE[self.projet.niveau_priorite] if self.projet else RANG_PRIORITE[NiveauPriorite.NORMALE]
        rang_academique = RANG_STATUT_ACADEMIQUE.get(self.demandeur.statut_academique, 0)
        return (rang_projet, rang_academique)

    def rang_dans_la_file(self, concurrentes):
        """
        Position (1 = en tête) parmi les demandes concurrentes : priorité
        décroissante, puis premier arrivé, premier servi. date_creation vaut
        None pour une demande pas encore enregistrée : elle passe après les
        demandes existantes de même priorité.
        """
        ma_cle = self.cle_priorite()
        devant = 0
        for c in concurrentes:
            cle = c.cle_priorite()
            if cle > ma_cle or (cle == ma_cle and (self.date_creation is None or c.date_creation < self.date_creation)):
                devant += 1
        return devant + 1

    # --- Création ---

    def preparer(self, equipements_ids):
        """
        Contrôles qui ne dépendent pas des autres réservations : cohérence
        des champs (full_clean -> clean()), créneau, appartenance des
        équipements au labo. Partagé par creer() et par la vérification
        préalable affichée avant confirmation (ReservationViewSet.verifier).
        'equipements' est exclu de full_clean car un ManyToMany ne peut être
        rempli qu'après la sauvegarde (il faut une clé primaire).
        """
        self.full_clean(exclude=['equipements'])
        self._verifier_creneau()
        self._verifier_equipements(self.laboratoire, equipements_ids)

    def statut_initial(self, equipements, a_des_concurrentes):
        """Renvoie (statut, explication) selon les règles de gestion."""
        from apps.utilisateurs.models import Role

        if any(e.necessite_validation for e in equipements):
            # RG2 : la sensibilité de la ressource prime sur le rôle demandeur.
            return StatutReservation.EN_ATTENTE, "Un équipement sensible nécessite une validation."
        if self.demandeur.role == Role.ETUDIANT:
            return StatutReservation.EN_ATTENTE, "Les demandes des étudiants sont validées par leur encadrant ou un technicien."
        if a_des_concurrentes:
            # Confirmer d'office court-circuiterait la file : une demande
            # déjà en attente, peut-être plus prioritaire, serait écartée
            # sans arbitrage. La nouvelle demande rejoint donc la file.
            return StatutReservation.EN_ATTENTE, "D'autres demandes sont en attente sur ce créneau : un validateur arbitrera selon les priorités."
        return StatutReservation.VALIDEE, "Réservation confirmée immédiatement."

    def creer(self, equipements_ids=None):
        """
        Point d'entrée unique de création d'une réservation. Ordre des
        contrôles : preparer(), puis sous verrou disponibilité et conflit
        avec les réservations acquises, puis choix du statut initial.
        """
        equipements_ids = equipements_ids or []
        self.preparer(equipements_ids)

        # select_for_update() pose un verrou sur les lignes des équipements
        # demandés : si deux personnes réservent le même équipement au même
        # instant, la seconde attend que la première ait fini, puis voit sa
        # réservation et détecte le conflit. Le tri par id impose un ordre de
        # verrouillage identique pour tous, ce qui évite les interblocages.
        with transaction.atomic():
            equipements = list(
                Equipement.objects.select_for_update().filter(id__in=equipements_ids).order_by('id')
            )
            self._verifier_disponibilite(self.laboratoire, equipements)

            if self._a_un_conflit(self.date, self.heure_debut, self.heure_fin, equipements_ids):
                raise ValidationError("Un des équipements sélectionnés est déjà réservé sur ce créneau.")
            self._verifier_maintenance(equipements_ids)

            a_des_concurrentes = self.concurrentes(equipements_ids).exists()
            self.statut, _ = self.statut_initial(equipements, a_des_concurrentes)

            self.save()
            # Le ManyToMany est rattaché seulement maintenant que self.pk existe.
            if equipements_ids:
                self.equipements.set(equipements_ids)
        return self

    # --- Transitions de statut ---
    # EN_ATTENTE -> VALIDEE | REFUSEE ; EN_ATTENTE/VALIDEE -> ANNULEE ;
    # VALIDEE -> TERMINEE (automatiquement, une fois le créneau passé).
    # validateur + date_validation gardent la trace de QUI a décidé et QUAND.

    def necessite_technicien(self):
        return self.equipements.filter(necessite_validation=True).exists()

    def peut_statuer(self, validateur):
        """
        Technicien et admin traitent toutes les demandes de leur
        établissement. Un enseignant-chercheur ne traite que celles des
        étudiants qu'il encadre, et pas sur un équipement sensible (la
        faisabilité technique relève alors du technicien).
        """
        from apps.utilisateurs.models import Role

        if validateur.role in [Role.ADMIN, Role.TECHNICIEN]:
            return True
        if validateur.role == Role.CHERCHEUR:
            return self.demandeur.encadrant_id == validateur.id and not self.necessite_technicien()
        return False

    def _verifier_decision(self, validateur):
        from apps.utilisateurs.models import Role

        # Seule une demande en attente peut être validée ou refusée : on ne
        # « ressuscite » pas une réservation annulée ou refusée.
        if self.statut != StatutReservation.EN_ATTENTE:
            raise ValidationError("Seule une réservation en attente peut être validée ou refusée.")
        # Séparation des rôles : on ne valide pas sa propre demande (sinon un
        # chercheur contournerait la règle des équipements sensibles).
        # L'admin, autorité finale de la plateforme, fait exception.
        if self.demandeur_id == validateur.id and validateur.role != Role.ADMIN:
            raise ValidationError("Vous ne pouvez pas statuer sur votre propre demande.")
        if not self.peut_statuer(validateur):
            raise ValidationError(
                "Seul l'encadrant de l'étudiant, un technicien ou un administrateur peut statuer sur cette demande."
            )

    def valider(self, validateur):
        """
        Valide la demande et renvoie la liste des demandes concurrentes
        refusées automatiquement (la vue prévient leurs demandeurs).
        """
        self._verifier_decision(validateur)
        equipements_ids = list(self.equipements.values_list('id', flat=True))

        with transaction.atomic():
            list(Equipement.objects.select_for_update().filter(id__in=equipements_ids).order_by('id'))
            # Une demande restée trop longtemps en file n'est plus valable.
            if datetime.combine(self.date, self.heure_debut) < timezone.localtime().replace(tzinfo=None):
                raise ValidationError("Le créneau de cette demande est déjà passé : refusez-la.")
            if self._a_un_conflit(self.date, self.heure_debut, self.heure_fin, equipements_ids, exclure_pk=self.pk):
                raise ValidationError("Ce créneau a déjà été attribué à une autre réservation : refusez cette demande.")

            self.statut = StatutReservation.VALIDEE
            self.validateur = validateur
            self.date_validation = timezone.now()
            self.save(update_fields=['statut', 'validateur', 'date_validation'])

            refusees = list(self.concurrentes(equipements_ids).select_related('demandeur', 'laboratoire'))
            for concurrente in refusees:
                concurrente.statut = StatutReservation.REFUSEE
                concurrente.motif_refus = MOTIF_REFUS_CONCURRENCE
                concurrente.validateur = validateur
                concurrente.date_validation = timezone.now()
                concurrente.save(update_fields=['statut', 'motif_refus', 'validateur', 'date_validation'])
        return refusees

    def refuser(self, validateur, motif=''):
        self._verifier_decision(validateur)
        self.statut = StatutReservation.REFUSEE
        self.motif_refus = motif
        self.validateur = validateur
        self.date_validation = timezone.now()
        self.save(update_fields=['statut', 'motif_refus', 'validateur', 'date_validation'])

    def creneau_commence(self):
        return datetime.combine(self.date, self.heure_debut) <= timezone.localtime().replace(tzinfo=None)

    def annuler(self, par=None):
        """Renvoie True si la réservation occupait le créneau (il se libère)."""
        if self.statut not in [StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE]:
            raise ValidationError("Cette réservation ne peut plus être annulée.")
        # Un créneau commencé ou passé appartient à l'historique : l'annuler
        # fausserait les statistiques d'usage et ne libérerait rien.
        if self.creneau_commence():
            raise ValidationError("Impossible d'annuler une réservation dont le créneau a déjà commencé ou est passé.")
        liberait_creneau = self.statut == StatutReservation.VALIDEE
        self.statut = StatutReservation.ANNULEE
        self.annulee_par = par
        self.date_annulation = timezone.now()
        self.save(update_fields=['statut', 'annulee_par', 'date_annulation'])
        return liberait_creneau

    @property
    def est_archivable(self):
        return not self.est_archivee and self.statut in STATUTS_ARCHIVABLES

    def archiver(self, par=None):
        if self.est_archivee:
            raise ValidationError("Cette réservation est déjà archivée.")
        if self.statut not in STATUTS_ARCHIVABLES:
            raise ValidationError(
                "Seule une réservation refusée, annulée ou terminée peut être archivée."
            )
        self.est_archivee = True
        self.archivee_par = par
        self.date_archivage = timezone.now()
        self.save(update_fields=['est_archivee', 'archivee_par', 'date_archivage'])

    def desarchiver(self):
        if not self.est_archivee:
            raise ValidationError("Cette réservation n'est pas archivée.")
        # archivee_par est effacé : l'historique complet (qui a archivé puis
        # restauré, et quand) reste dans le journal d'activité.
        self.est_archivee = False
        self.archivee_par = None
        self.date_archivage = None
        self.save(update_fields=['est_archivee', 'archivee_par', 'date_archivage'])


class AlerteCreneau(models.Model):
    """
    Liste d'attente d'un créneau : l'utilisateur demande à être prévenu si
    le créneau qu'il visait se libère (annulation d'une réservation
    validée). Créée à sa demande après un conflit, ou automatiquement quand
    sa demande est refusée au profit d'une demande prioritaire.
    """
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='alertes_creneau'
    )
    laboratoire = models.ForeignKey(Laboratoire, on_delete=models.CASCADE, related_name='alertes_creneau')
    equipements = models.ManyToManyField(Equipement, blank=True, related_name='alertes_creneau')
    date = models.DateField()
    heure_debut = models.TimeField()
    heure_fin = models.TimeField()
    active = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_notification = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Alerte de créneau'
        verbose_name_plural = 'Alertes de créneau'
        ordering = ['date_creation']

    def __str__(self):
        return f'{self.utilisateur} — {self.date} {self.heure_debut:%H:%M}-{self.heure_fin:%H:%M}'

    @classmethod
    def correspondant_a(cls, reservation):
        """Alertes actives que la libération de cette réservation satisfait."""
        equipements_ids = list(reservation.equipements.values_list('id', flat=True))
        qs = cls.objects.filter(
            active=True, date=reservation.date, laboratoire=reservation.laboratoire,
            heure_debut__lt=reservation.heure_fin, heure_fin__gt=reservation.heure_debut,
        ).exclude(utilisateur=reservation.demandeur)
        if equipements_ids:
            qs = qs.filter(equipements__id__in=equipements_ids)
        return qs.distinct().select_related('utilisateur')
