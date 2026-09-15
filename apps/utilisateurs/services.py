import secrets
import string
from django.core.mail import send_mail
from django.conf import settings


def generer_mot_de_passe(longueur=12):
    """
    Génère un mot de passe aléatoire fort, jamais choisi par l'admin.
    On garantit un minimum de diversité pour éviter un mot de passe
    faible malgré la longueur (ex: que des minuscules).
    """
    alphabet = string.ascii_letters + string.digits + '!@#$%&*'
    while True:
        mdp = ''.join(secrets.choice(alphabet) for _ in range(longueur))
        if (any(c.isupper() for c in mdp)
                and any(c.isdigit() for c in mdp)
                and any(c in '!@#$%&*' for c in mdp)):
            return mdp


def envoyer_identifiants(utilisateur, mot_de_passe):
    lien_connexion = f'{settings.FRONTEND_URL}/connexion'
    message = (
        f'Bonjour {utilisateur.prenom},\n\n'
        f"Un compte vient d'être créé pour vous sur la plateforme UMRED Labo.\n\n"
        f'Email : {utilisateur.email}\n'
        f'Mot de passe temporaire : {mot_de_passe}\n\n'
        f'Connectez-vous ici : {lien_connexion}\n\n'
        f'Nous vous recommandons de changer ce mot de passe dès votre première connexion.\n\n'
        f"L'équipe UMRED Labo"
    )
    send_mail(
        subject='Votre compte UMRED Labo a été créé',
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[utilisateur.email],
        fail_silently=False,
    )