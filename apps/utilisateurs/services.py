from django.core.mail import send_mail
from django.conf import settings


def envoyer_lien_definition_mdp(utilisateur, jeton: str):
    lien = f'{settings.FRONTEND_URL}/definir-mot-de-passe/{jeton}'
    message = (
        f'Bonjour {utilisateur.prenom},\n\n'
        f"Un compte vient d'être créé pour vous sur la plateforme UMRED Labo.\n\n"
        f'Cliquez sur ce lien pour choisir votre mot de passe et activer votre accès :\n{lien}\n\n'
        f'Ce lien est valable 3 jours.\n\n'
        f"L'équipe UMRED Labo"
    )
    send_mail(
        subject='Activez votre compte UMRED Labo',
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[utilisateur.email],
        fail_silently=False,
    )