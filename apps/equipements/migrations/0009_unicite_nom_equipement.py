"""
Unicité du nom d'équipement à l'intérieur d'un même laboratoire.

Renomme d'abord les doublons existants (suffixe « (2) »...), sans rien
supprimer, puis ajoute la contrainte Lower(nom) + laboratoire.
"""
import unicodedata

from django.db import migrations, models
from django.db.models.functions import Lower


def _cle(valeur):
    valeur = " ".join((valeur or "").split())
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", valeur) if unicodedata.category(c) != "Mn")
    return sans_accents.casefold()


def dedupliquer(apps, schema_editor):
    Equipement = apps.get_model("equipements", "Equipement")
    vus = {}
    renommes = []
    for equip in Equipement.objects.order_by("id"):
        cle = (equip.laboratoire_id, _cle(equip.nom))
        if cle not in vus:
            vus[cle] = True
            continue
        ancien, n = equip.nom, 2
        while (equip.laboratoire_id, _cle(f"{ancien} ({n})")) in vus:
            n += 1
        equip.nom = f"{ancien} ({n})"
        vus[(equip.laboratoire_id, _cle(equip.nom))] = True
        equip.save(update_fields=["nom"])
        renommes.append(f"  Équipement #{equip.id} (labo {equip.laboratoire_id}) : « {ancien} » -> « {equip.nom} »")
    if renommes:
        print("\n[equipements] Doublons de nom renommés :\n" + "\n".join(renommes))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("equipements", "0008_equipement_categorie")]

    operations = [
        migrations.RunPython(dedupliquer, noop),
        migrations.AddConstraint(
            model_name="equipement",
            constraint=models.UniqueConstraint(
                Lower("nom"), "laboratoire", name="uniq_equipement_nom_par_laboratoire"
            ),
        ),
    ]
