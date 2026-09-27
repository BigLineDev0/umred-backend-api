from datetime import date

from rest_framework.exceptions import APIException, ValidationError


class SuppressionImpossible(APIException):
    """
    409 Conflict : la ressource est liée à un historique (réservations,
    maintenances...) qu'une suppression effacerait en cascade. Même
    principe que pour les comptes utilisateurs : on désactive, on ne
    supprime pas.
    """
    status_code = 409
    default_code = 'suppression_impossible'


def lire_date(request, nom):
    """
    Lit un paramètre de requête au format AAAA-MM-JJ. Sans cette
    vérification, une valeur invalide (?date_debut=abc) arrivait telle
    quelle dans le filtre SQL et provoquait une erreur 500 ; ici elle
    produit une erreur 400 explicite.
    """
    valeur = request.query_params.get(nom)
    if not valeur:
        return None
    try:
        return date.fromisoformat(valeur)
    except ValueError:
        raise ValidationError({nom: 'Date invalide, format attendu : AAAA-MM-JJ.'})


def lire_id(request, nom):
    """Même principe pour un identifiant : ?laboratoire=abc -> 400, pas 500."""
    valeur = request.query_params.get(nom)
    if not valeur:
        return None
    try:
        return int(valeur)
    except ValueError:
        raise ValidationError({nom: 'Identifiant invalide, un nombre entier est attendu.'})
