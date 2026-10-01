from django.core.mail import send_mail
from django.conf import settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken


def envoyer_lien_definition_mdp(utilisateur, jeton: str):
    lien = f'{settings.FRONTEND_URL}/definir-mot-de-passe/{jeton}'
    message = (
        f'Bonjour {utilisateur.prenom},\n\n'
        f"Un compte vient d'être créé pour vous sur la plateforme UMRED.\n\n"
        f'Cliquez sur ce lien pour choisir votre mot de passe et activer votre accès :\n{lien}\n\n'
        f'Ce lien est valable 3 jours.\n\n'
        f"L'équipe UMRED"
    )
    send_mail(
        subject='Activez votre compte UMRED',
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[utilisateur.email],
        fail_silently=False,
    )


def envoyer_lien_reinitialisation_mdp(utilisateur, jeton: str):
    # Même page frontend que l'invitation : seul le texte de l'email change.
    lien = f'{settings.FRONTEND_URL}/definir-mot-de-passe/{jeton}'
    message = (
        f'Bonjour {utilisateur.prenom},\n\n'
        f'Une réinitialisation de mot de passe a été demandée pour votre compte UMRED.\n\n'
        f'Cliquez sur ce lien pour choisir un nouveau mot de passe :\n{lien}\n\n'
        f'Ce lien est valable 1 heure. Si vous n\'êtes pas à l\'origine de cette demande, '
        f'ignorez cet email : votre mot de passe actuel reste inchangé.\n\n'
        f"L'équipe UMRED"
    )
    send_mail(
        subject='Réinitialisation de votre mot de passe UMRED',
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[utilisateur.email],
        fail_silently=False,
    )


def envoyer_lien_activation(utilisateur, jeton: str):
    # Inscription libre : le lien prouve que l'adresse appartient bien à
    # la personne qui s'inscrit ; le compte reste bloqué tant qu'il n'est
    # pas cliqué.
    lien = f'{settings.FRONTEND_URL}/activer-compte/{jeton}'
    message = (
        f'Bonjour {utilisateur.prenom},\n\n'
        f'Merci pour votre inscription sur la plateforme UMRED.\n\n'
        f'Cliquez sur ce lien pour confirmer votre adresse email et activer votre compte :\n{lien}\n\n'
        f'Ce lien est valable 24 heures. Si vous n\'êtes pas à l\'origine de cette inscription, '
        f'ignorez cet email : aucun compte ne sera activé.\n\n'
        f"L'équipe UMRED"
    )
    send_mail(
        subject='Confirmez votre adresse email UMRED',
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[utilisateur.email],
        fail_silently=False,
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
