from datetime import datetime, time

from django.conf import settings
from django.db import models, transaction
from django.core.exceptions import ValidationError
from apps.laboratoires.models import Laboratoire, StatutLaboratoire
from apps.equipements.models import Equipement, StatutEquipement
from django.utils import timezone

# Amplitude d'ouverture des laboratoires : une réservation doit tenir
# entièrement dans cette plage.
HEURE_OUVERTURE = time(8, 0)
HEURE_FERMETURE = time(19, 0)

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
    projet = models.ForeignKey(
        'projets.Projet', on_delete=models.SET_NULL, null=True, blank=True, related_name='reservations'
    )
    est_archivee = models.BooleanField(default=False)
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

    def clean(self):
        if self.heure_fin <= self.heure_debut:
            raise ValidationError("L'heure de fin doit être après l'heure de début.")

    def _verifier_creneau(self):
        # Pas de réservation dans le passé (ni plus tôt aujourd'hui) et
        # uniquement pendant les heures d'ouverture du laboratoire.
        maintenant = timezone.localtime()
        if datetime.combine(self.date, self.heure_debut) < maintenant.replace(tzinfo=None):
            raise ValidationError("Impossible de réserver un créneau déjà passé.")
        if self.heure_debut < HEURE_OUVERTURE or self.heure_fin > HEURE_FERMETURE:
            raise ValidationError(
                f"Les réservations sont possibles entre {HEURE_OUVERTURE:%H:%M} et {HEURE_FERMETURE:%H:%M}."
            )

    @staticmethod
    def _verifier_disponibilite(laboratoire, equipements):
        if laboratoire.statut == StatutLaboratoire.INDISPONIBLE:
            raise ValidationError("Ce laboratoire est actuellement indisponible.")
        indisponibles = [e.nom for e in equipements if e.statut in STATUTS_EQUIPEMENT_NON_RESERVABLES]
        if indisponibles:
            raise ValidationError(
                f"Équipement(s) non réservable(s) (panne, maintenance ou hors service) : {', '.join(indisponibles)}."
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

    @classmethod
    def _a_un_conflit(cls, date, heure_debut, heure_fin, equipements_ids, exclure_pk=None):
        """
        Vérifie le chevauchement pour CHAQUE équipement sélectionné :
        s'il en existe un seul déjà pris sur ce créneau, la réservation
        entière est refusée (pas de réservation partielle possible).
        """
        if not equipements_ids:
            return False
        # Deux créneaux [A_debut, A_fin[ et [B_debut, B_fin[ se chevauchent
        # si et seulement si A_debut < B_fin ET A_fin > B_debut. D'où les
        # deux filtres heure_debut__lt / heure_fin__gt. Les inégalités
        # strictes permettent d'enchaîner deux créneaux bord à bord
        # (10h-12h puis 12h-14h ne sont pas en conflit).
        # Une demande EN_ATTENTE bloque déjà le créneau : on évite ainsi que
        # deux demandes concurrentes soient toutes deux validées ensuite.
        qs = cls.objects.filter(
            equipements__id__in=equipements_ids,
            date=date,
            statut__in=[StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE],
            heure_debut__lt=heure_fin,
            heure_fin__gt=heure_debut,
        ).distinct()
        if exclure_pk:
            qs = qs.exclude(pk=exclure_pk)
        return qs.exists()

    def creer(self, equipements_ids=None):
        """
        Point d'entrée unique de création d'une réservation. Ordre des
        contrôles : cohérence des champs (full_clean -> clean()), appartenance
        des équipements au labo, absence de conflit, puis choix du statut
        initial selon les règles de gestion.
        """
        # Import local pour éviter un import circulaire
        # (utilisateurs -> reservations -> utilisateurs).
        from apps.utilisateurs.models import Role

        equipements_ids = equipements_ids or []

        # 'equipements' est exclu car un ManyToMany ne peut être rempli
        # qu'après la sauvegarde (il faut une clé primaire).
        self.full_clean(exclude=['equipements'])
        self._verifier_creneau()
        self._verifier_equipements(self.laboratoire, equipements_ids)

        # Tout ce qui suit s'exécute dans une transaction. select_for_update()
        # pose un verrou sur les lignes des équipements demandés : si deux
        # personnes réservent le même équipement au même instant, la seconde
        # attend que la première ait fini, puis voit sa réservation et
        # détecte le conflit. Sans verrou, les deux vérifications pourraient
        # passer avant qu'aucune insertion n'ait eu lieu (double réservation).
        # Le tri par id impose un ordre de verrouillage identique pour tous,
        # ce qui évite les interblocages (deadlocks).
        with transaction.atomic():
            equipements = list(
                Equipement.objects.select_for_update().filter(id__in=equipements_ids).order_by('id')
            )
            self._verifier_disponibilite(self.laboratoire, equipements)

            if self._a_un_conflit(self.date, self.heure_debut, self.heure_fin, equipements_ids):
                raise ValidationError(
                    "Un des équipements sélectionnés est déjà réservé ou en attente sur ce créneau."
                )

            # Un équipement sensible force toujours la validation, même pour un
            # rôle qui bénéficierait normalement d'une confirmation immédiate
            # (RG2) — la sensibilité de la ressource prime sur le rôle demandeur.
            equipement_sensible = any(e.necessite_validation for e in equipements)

            # Étudiant -> toujours validation humaine. Chercheur/technicien/admin
            # -> confirmation immédiate, sauf équipement sensible.
            if equipement_sensible or self.demandeur.role == Role.ETUDIANT:
                self.statut = StatutReservation.EN_ATTENTE
            else:
                self.statut = StatutReservation.VALIDEE

            self.save()
            # Le ManyToMany est rattaché seulement maintenant que self.pk existe.
            if equipements_ids:
                self.equipements.set(equipements_ids)
        return self

    # --- Transitions de statut ---
    # EN_ATTENTE -> VALIDEE | REFUSEE ; EN_ATTENTE/VALIDEE -> ANNULEE.
    # L'archivage est indépendant du statut : il masque seulement la
    # réservation des listes par défaut, sans rien supprimer.
    # validateur + date_validation gardent la trace de QUI a décidé et QUAND.

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

    def valider(self, validateur):
        self._verifier_decision(validateur)
        self.statut = StatutReservation.VALIDEE
        self.validateur = validateur
        self.date_validation = timezone.now()
        self.save(update_fields=['statut', 'validateur', 'date_validation'])

    def refuser(self, validateur):
        self._verifier_decision(validateur)
        self.statut = StatutReservation.REFUSEE
        self.validateur = validateur
        self.date_validation = timezone.now()
        self.save(update_fields=['statut', 'validateur', 'date_validation'])

    def annuler(self):
        if self.statut not in [StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE]:
            raise ValidationError("Cette réservation ne peut plus être annulée.")
        self.statut = StatutReservation.ANNULEE
        self.save(update_fields=['statut'])

    def archiver(self):
        self.est_archivee = True
        self.save(update_fields=['est_archivee'])

    def desarchiver(self):
        self.est_archivee = False
        self.save(update_fields=['est_archivee'])