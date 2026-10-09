from django.conf import settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.notifications.emails import envoyer_email


def envoyer_lien_definition_mdp(utilisateur, jeton: str):
    lien = f'{settings.FRONTEND_URL}/definir-mot-de-passe/{jeton}'
    etablissement = f' par {utilisateur.organisation.nom}' if utilisateur.organisation else ''
    envoyer_email(
        utilisateur, 'Activez votre compte SenLab',
        [f"Un compte vient d'être créé pour vous sur la plateforme SenLab{etablissement}.",
         'Choisissez votre mot de passe pour activer votre accès.'],
        titre='Bienvenue sur SenLab',
        bouton=('Choisir mon mot de passe', lien),
        note='Ce lien est valable 3 jours.',
    )


def envoyer_lien_reinitialisation_mdp(utilisateur, jeton: str):
    # Même page frontend que l'invitation : seul le texte de l'email change.
    lien = f'{settings.FRONTEND_URL}/definir-mot-de-passe/{jeton}'
    envoyer_email(
        utilisateur, 'Réinitialisation de votre mot de passe SenLab',
        ['Une réinitialisation de mot de passe a été demandée pour votre compte SenLab.'],
        titre='Réinitialiser votre mot de passe',
        bouton=('Choisir un nouveau mot de passe', lien),
        note="Ce lien est valable 1 heure. Si vous n'êtes pas à l'origine de cette demande, "
             'ignorez cet email : votre mot de passe actuel reste inchangé.',
    )


def envoyer_lien_activation(utilisateur, jeton: str):
    # Inscription libre : le lien prouve que l'adresse appartient bien à
    # la personne qui s'inscrit ; le compte reste bloqué tant qu'il n'est
    # pas cliqué.
    lien = f'{settings.FRONTEND_URL}/activer-compte/{jeton}'
    envoyer_email(
        utilisateur, 'Confirmez votre adresse email SenLab',
        ['Merci pour votre inscription sur la plateforme SenLab.',
         'Confirmez votre adresse email pour activer votre compte.'],
        titre='Confirmez votre adresse email',
        bouton=('Confirmer mon adresse', lien),
        note="Ce lien est valable 24 heures. Si vous n'êtes pas à l'origine de cette inscription, "
             'ignorez cet email : aucun compte ne sera activé.',
    )


def revoquer_sessions(utilisateur):
    """
    Met sur liste noire tous les refresh tokens de l'utilisateur : après un
    changement de mot de passe ou une désactivation, aucune session ouverte
    ailleurs (autre navigateur, appareil volé...) ne peut obtenir de nouveau
    token d'accès. Les tokens d'accès déjà émis expirent d'eux-mêmes
    (30 min max).
    """
    for token in OutstandingToken.objects.filter(user=utilisateur, blacklistedtoken__isnull=True):
        BlacklistedToken.objects.get_or_create(token=token)
