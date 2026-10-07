"""
Unicité du consommable dans un laboratoire : nom unique, et référence
unique quand elle est renseignée (les références vides ne s'opposent pas).

Renomme les noms en double (suffixe « (2) »...) et préfixe les références
en double (« REF » -> « REF-D2 »), sans rien supprimer, puis pose les
contraintes.
"""
import unicodedata

from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


def _cle(valeur):
    valeur = " ".join((valeur or "").split())
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", valeur) if unicodedata.category(c) != "Mn")
    return sans_accents.casefold()


def dedupliquer(apps, schema_editor):
    Consommable = apps.get_model("consommables", "Consommable")
    noms_vus, refs_vues, renommes = {}, {}, []
    for conso in Consommable.objects.order_by("id"):
        modifie = []

        cle_nom = (conso.laboratoire_id, _cle(conso.nom))
        if cle_nom in noms_vus:
            ancien, n = conso.nom, 2
            while (conso.laboratoire_id, _cle(f"{ancien} ({n})")) in noms_vus:
                n += 1
            conso.nom = f"{ancien} ({n})"
            modifie.append(f"nom « {ancien} » -> « {conso.nom} »")
        noms_vus[(conso.laboratoire_id, _cle(conso.nom))] = True

        if conso.reference:
            cle_ref = (conso.laboratoire_id, _cle(conso.reference))
            if cle_ref in refs_vues:
                ancien, n = conso.reference, 2
                while (conso.laboratoire_id, _cle(f"{ancien}-D{n}")) in refs_vues:
                    n += 1
                conso.reference = f"{ancien}-D{n}"
                modifie.append(f"référence « {ancien} » -> « {conso.reference} »")
            refs_vues[(conso.laboratoire_id, _cle(conso.reference))] = True

        if modifie:
            conso.save(update_fields=["nom", "reference"])
            renommes.append(f"  Consommable #{conso.id} (labo {conso.laboratoire_id}) : " + " ; ".join(modifie))
    if renommes:
        print("\n[consommables] Doublons renommés :\n" + "\n".join(renommes))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("consommables", "0001_initial")]

    operations = [
        migrations.RunPython(dedupliquer, noop),
        migrations.AddConstraint(
            model_name="consommable",
            constraint=models.UniqueConstraint(
                Lower("nom"), "laboratoire", name="uniq_consommable_nom_par_laboratoire"
            ),
        ),
        migrations.AddConstraint(
            model_name="consommable",
            constraint=models.UniqueConstraint(
                Lower("reference"), "laboratoire",
                condition=~Q(reference=""), name="uniq_consommable_reference_par_laboratoire",
            ),
        ),
    ]
