"""
Isolation des données entre organisations (multi-tenant sur base partagée).

Toutes les tables restent communes ; chaque requête est filtrée sur
l'organisation de l'utilisateur connecté en suivant le chemin de clés
étrangères qui mène du modèle à l'organisation (ex. une réservation :
laboratoire -> organisation). Deux garde-fous complémentaires :

1. en LECTURE, filtrer_par_organisation() restreint chaque queryset ;
2. en ÉCRITURE, ChampsOrganisationMixin restreint les clés étrangères
   acceptées par les serializers : impossible de rattacher sa réservation
   à l'équipement d'un autre établissement en envoyant son id.
"""
from dataclasses import dataclass
from datetime import time

from rest_framework.relations import ManyRelatedField

# Valeur du rôle, en dur pour éviter un import circulaire avec utilisateurs.
ROLE_SUPER_ADMIN = 'SUPER_ADMIN'


def est_super_admin(user):
    return getattr(user, 'role', None) == ROLE_SUPER_ADMIN


def filtrer_par_organisation(qs, user, chemin='organisation'):
    """
    Le super-admin pilote la plateforme (organisations, statistiques
    globales) mais ne consulte pas les données métier des établissements :
    il ne voit donc rien dans les listes « tenant ».
    """
    if est_super_admin(user):
        return qs.none()
    return qs.filter(**{chemin: user.organisation_id})


class ChampsOrganisationMixin:
    """
    À placer AVANT serializers.ModelSerializer dans l'héritage.
    champs_organisation = {'nom_du_champ': 'chemin__vers__organisation'}
    """
    champs_organisation: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return
        for nom, chemin in self.champs_organisation.items():
            champ = self.fields.get(nom)
            if champ is None or champ.read_only:
                continue
            relation = champ.child_relation if isinstance(champ, ManyRelatedField) else champ
            if getattr(relation, 'queryset', None) is not None:
                relation.queryset = filtrer_par_organisation(relation.queryset, user, chemin)


@dataclass(frozen=True)
class ReglesReservation:
    heure_ouverture: time
    heure_fermeture: time
    duree_min: int  # minutes
    duree_max: int  # minutes
    delai_max_jours: int


REGLES_PAR_DEFAUT = ReglesReservation(time(8, 0), time(19, 0), 30, 480, 60)


def regles_reservation(organisation):
    """Règles de l'établissement, ou valeurs par défaut s'il n'y en a pas."""
    if organisation is None:
        return REGLES_PAR_DEFAUT
    return ReglesReservation(
        heure_ouverture=organisation.heure_ouverture,
        heure_fermeture=organisation.heure_fermeture,
        duree_min=organisation.duree_min_reservation,
        duree_max=organisation.duree_max_reservation,
        delai_max_jours=organisation.delai_max_reservation_jours,
    )
