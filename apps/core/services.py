from .models import JournalActivite


def enregistrer(auteur, action, instance=None, description=''):
    """
    Point unique d'écriture du journal d'audit, importé partout sous le nom
    journaliser(). Passer instance renseigne automatiquement entite_type +
    entite_id via la GenericForeignKey.
    """
    JournalActivite.objects.create(
        auteur=auteur,
        action=action,
        description=description,
        entite=instance,
    )