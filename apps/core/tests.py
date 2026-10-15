from datetime import time, timedelta
from io import BytesIO

import openpyxl
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
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


class SyntheseIAValidationTests(SimpleTestCase):
    """Le texte reçu de n8n (rédigé par le service IA) n'est utilisé que s'il a la forme attendue."""

    def test_synthese_correcte_acceptee(self):
        from .synthese import synthese_valide
        self.assertEqual(synthese_valide('  Semaine calme.  '), 'Semaine calme.')

    def test_textes_invalides_refuses(self):
        from .synthese import LONGUEUR_MAX_SYNTHESE, synthese_valide
        for texte in [None, [], {}, 42, '   ', 'x' * (LONGUEUR_MAX_SYNTHESE + 1)]:
            self.assertIsNone(synthese_valide(texte), texte)


@override_settings(CONTACT_EMAIL='contact@senlab.test')
class ContactTests(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.donnees = {'nom': 'Awa Diop', 'email': 'awa@exemple.sn', 'sujet': 'acces',
                        'message': 'Je souhaite ouvrir un accès pour mon laboratoire.'}

    def test_message_envoye_avec_reponse_a_l_expediteur(self):
        from django.core import mail
        reponse = self.client.post('/api/contact/', self.donnees, format='json')
        self.assertEqual(reponse.status_code, 202)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['contact@senlab.test'])
        self.assertEqual(mail.outbox[0].reply_to, ['awa@exemple.sn'])
        self.assertIn('Je souhaite ouvrir un accès', mail.outbox[0].body)

    def test_robot_ignore_sans_envoi(self):
        from django.core import mail
        reponse = self.client.post('/api/contact/', {**self.donnees, 'site_web': 'http://spam.example'}, format='json')
        self.assertEqual(reponse.status_code, 202)
        self.assertEqual(len(mail.outbox), 0)

    def test_message_trop_court_refuse(self):
        reponse = self.client.post('/api/contact/', {**self.donnees, 'message': 'Salut'}, format='json')
        self.assertEqual(reponse.status_code, 400)

    @override_settings(CONTACT_EMAIL='')
    def test_formulaire_non_configure(self):
        self.assertEqual(self.client.post('/api/contact/', self.donnees, format='json').status_code, 503)
