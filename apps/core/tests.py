from datetime import time, timedelta
from io import BytesIO

import openpyxl
from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.laboratoires.models import Laboratoire
from apps.reservations.models import Reservation
from apps.utilisateurs.models import Utilisateur, Role
from .views import _cellule_sure


class CelluleSureTests(SimpleTestCase):
    def test_formule_neutralisee(self):
        self.assertEqual(_cellule_sure('=HYPERLINK("http://x")'), '\'=HYPERLINK("http://x")')
        self.assertEqual(_cellule_sure('+33'), "'+33")

    def test_valeurs_normales_intactes(self):
        self.assertEqual(_cellule_sure('Labo A'), 'Labo A')
        self.assertEqual(_cellule_sure(12.5), 12.5)


class ExportExcelTests(APITestCase):
    def test_nom_malveillant_exporte_comme_texte(self):
        cache.clear()
        admin = Utilisateur.objects.create_user(email='admin@umred.sn', password='x', nom='A', prenom='A', role=Role.ADMIN)
        pirate = Utilisateur.objects.create_user(email='p@umred.sn', password='x', nom='=1+1', prenom='X', role=Role.ETUDIANT)
        labo = Laboratoire.objects.create(nom='Labo', localisation='Bât. 1')
        Reservation.objects.create(demandeur=pirate, laboratoire=labo, date=timezone.localdate() + timedelta(days=1),
                                   heure_debut=time(10), heure_fin=time(11), motif='TP')

        self.client.force_authenticate(admin)
        reponse = self.client.get('/api/rapports/export/')
        self.assertEqual(reponse.status_code, 200)

        ws = openpyxl.load_workbook(BytesIO(reponse.content))['Détail des réservations']
        cellule = ws['F2']
        self.assertEqual(cellule.data_type, 's')
