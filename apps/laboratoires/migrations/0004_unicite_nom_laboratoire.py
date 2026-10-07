"""
Unicité du nom de laboratoire par organisation.

1. Migration de DONNÉES : renomme les doublons déjà en base (suffixe
   « (2) », « (3) »...) de façon déterministe et les journalise. Aucune
   suppression.
2. Contrainte de base : UniqueConstraint sur Lower(nom) + organisation.

La comparaison de doublons est insensible à la casse, aux accents et aux
espaces superflus (même logique que apps/core/validation.cle_unicite).
"""
import unicodedata

from django.db import migrations, models
from django.db.models.functions import Lower


def _cle(valeur):
    valeur = " ".join((valeur or "").split())
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", valeur) if unicodedata.category(c) != "Mn")
    return sans_accents.casefold()


def _renommer_doublons(apps, groupes_champ):
    Laboratoire = apps.get_model("laboratoires", "Laboratoire")
    vus = {}
    renommes = []
    for labo in Laboratoire.objects.order_by("id"):
        cle = (labo.organisation_id, _cle(labo.nom))
        if cle not in vus:
            vus[cle] = 1
            continue
        vus[cle] += 1
        ancien = labo.nom
        # Trouve un suffixe libre dans l'organisation.
        n = vus[cle]
        while True:
            candidat = f"{ancien} ({n})"
            if (labo.organisation_id, _cle(candidat)) not in vus:
                break
            n += 1
        vus[(labo.organisation_id, _cle(candidat))] = 1
        labo.nom = candidat
        labo.save(update_fields=["nom"])
        renommes.append(f"  Laboratoire #{labo.id} (org {labo.organisation_id}) : « {ancien} » -> « {candidat} »")
    if renommes:
        print("\n[laboratoires] Doublons de nom renommés :\n" + "\n".join(renommes))


def dedupliquer(apps, schema_editor):
    _renommer_doublons(apps, None)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("laboratoires", "0003_laboratoire_organisation")]

    operations = [
        migrations.RunPython(dedupliquer, noop),
        migrations.AddConstraint(
            model_name="laboratoire",
            constraint=models.UniqueConstraint(
                Lower("nom"), "organisation", name="uniq_labo_nom_par_organisation"
            ),
        ),
    ]
