from .models import JournalActivite


def enregistrer(auteur, action, instance=None, description=''):
    JournalActivite.objects.create(
        auteur=auteur,
        action=action,
        description=description,
        entite=instance,
    )