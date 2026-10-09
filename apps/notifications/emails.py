"""
Mise en page commune des emails : version HTML (gabarit compatible avec
les messageries : tableaux et styles en ligne) et version texte, envoyées
ensemble. La couleur et le nom de l'établissement du destinataire
personnalisent l'en-tête.
"""
import re

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

COULEUR_SENLAB = '#1B2CC1'
# Couleur par défaut de l'ancienne charte, encore celle du modèle : un
# établissement qui ne l'a jamais modifiée reçoit la charte SenLab (même
# règle que le frontend).
COULEURS_HERITEES = {'#1848D9'}
_HEXA = re.compile(r'^#[0-9A-Fa-f]{6}$')


def _couleur(organisation):
    couleur = getattr(organisation, 'couleur_primaire', '') or ''
    if not _HEXA.match(couleur) or couleur.upper() in COULEURS_HERITEES:
        return COULEUR_SENLAB
    return couleur


def envoyer_email(destinataire, sujet, paragraphes, *, titre=None, bouton=None, note=None, fail_silently=False):
    """
    destinataire : un Utilisateur (prénom, email, établissement).
    paragraphes : liste de textes (les retours à la ligne sont conservés).
    bouton : (libellé, lien) de l'action principale, ou None.
    """
    organisation = getattr(destinataire, 'organisation', None)
    contexte = {
        'sujet': sujet,
        'titre': titre or sujet,
        'prenom': destinataire.prenom,
        'paragraphes': [p for p in paragraphes if p],
        'bouton': {'libelle': bouton[0], 'lien': bouton[1]} if bouton else None,
        'note': note,
        'etablissement': getattr(organisation, 'nom', ''),
        'couleur': _couleur(organisation),
        'apercu': next((p for p in paragraphes if p), '')[:140],
    }
    email = EmailMultiAlternatives(
        subject=sujet,
        body=render_to_string('emails/message.txt', contexte),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[destinataire.email],
    )
    email.attach_alternative(render_to_string('emails/message.html', contexte), 'text/html')
    email.send(fail_silently=fail_silently)
