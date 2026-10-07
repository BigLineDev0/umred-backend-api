from datetime import time, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.laboratoires.models import Laboratoire
from apps.reservations.models import Reservation, StatutReservation
from apps.utilisateurs.models import Utilisateur, Role
from .models import Equipement


class EquipementTests(APITestCase):
    def setUp(self):
        self.technicien = Utilisateur.objects.create_user(email='t@umred.sn', password='x', nom='T', prenom='T', role=Role.TECHNICIEN)
        self.labo = Laboratoire.objects.create(nom='Labo', localisation='Bât. 1')
        self.equipement = Equipement.objects.create(laboratoire=self.labo, nom='Microscope', numero_serie='M-1')
        self.client.force_authenticate(self.technicien)

    def reservation(self, jours):
        r = Reservation.objects.create(
            demandeur=self.technicien, laboratoire=self.labo, date=timezone.localdate() + timedelta(days=jours),
            heure_debut=time(8), heure_fin=time(12), motif='TP', statut=StatutReservation.VALIDEE,
        )
        r.equipements.set([self.equipement])
        return r

    def test_parametre_inconnu_ne_plante_pas(self):
        self.assertEqual(self.client.get('/api/equipements/?equipement=1').status_code, 200)

    def test_heures_futures_non_comptees(self):
        self.reservation(-1)
        self.reservation(+1)
        self.assertEqual(self.equipement.heures_utilisation_depuis_derniere_maintenance(), 4.0)

    def test_faux_pdf_refuse(self):
        faux = SimpleUploadedFile('manuel.pdf', b'<html>pas un pdf</html>', content_type='application/pdf')
        reponse = self.client.patch(f'/api/equipements/{self.equipement.id}/', {'manuel_pdf': faux}, format='multipart')
        self.assertEqual(reponse.status_code, 400)

    def test_vrai_pdf_accepte(self):
        vrai = SimpleUploadedFile('manuel.pdf', b'%PDF-1.4\n%%EOF', content_type='application/pdf')
        reponse = self.client.patch(f'/api/equipements/{self.equipement.id}/', {'manuel_pdf': vrai}, format='multipart')
        self.assertEqual(reponse.status_code, 200)

    def test_suppression_avec_historique_refusee(self):
        self.reservation(+1)
        self.assertEqual(self.client.delete(f'/api/equipements/{self.equipement.id}/').status_code, 409)
        self.assertTrue(Equipement.objects.filter(pk=self.equipement.pk).exists())

    def test_suppression_sans_historique_acceptee(self):
        self.assertEqual(self.client.delete(f'/api/equipements/{self.equipement.id}/').status_code, 204)

    def test_suppression_labo_non_vide_refusee(self):
        admin = Utilisateur.objects.create_user(email='a@umred.sn', password='x', nom='A', prenom='A', role=Role.ADMIN)
        self.client.force_authenticate(admin)
        self.assertEqual(self.client.delete(f'/api/laboratoires/{self.labo.id}/').status_code, 409)


class EquipementValidationTests(APITestCase):
    def setUp(self):
        self.tech = Utilisateur.objects.create_user(
            email='valid@test.sn', password='x', nom='V', prenom='V', role=Role.TECHNICIEN)
        self.labo = Laboratoire.objects.create(nom='Labo Valid', localisation='Bât. 1')
        self.autre_labo = Laboratoire.objects.create(nom='Autre labo', localisation='Bât. 2')
        Equipement.objects.create(laboratoire=self.labo, nom='Microscope optique', numero_serie='SN-BASE')
        self.client.force_authenticate(self.tech)

    def _creer(self, **kw):
        data = {'laboratoire': self.labo.id, 'nom': 'Nouvel appareil',
                'numero_serie': 'SN-X', 'statut': 'DISPONIBLE'}
        data.update(kw)
        return self.client.post('/api/equipements/', data, format='json')

    def test_doublon_meme_labo_insensible_casse_espaces(self):
        reponse = self._creer(nom='  microscope   OPTIQUE ', numero_serie='SN-2')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('nom', reponse.json())

    def test_meme_nom_dans_autre_labo_autorise(self):
        reponse = self._creer(laboratoire=self.autre_labo.id, nom='Microscope optique', numero_serie='SN-3')
        self.assertEqual(reponse.status_code, 201)

    def test_nom_invalide(self):
        self.assertEqual(self._creer(nom='<b>x</b>', numero_serie='SN-4').status_code, 400)
        self.assertEqual(self._creer(nom='123', numero_serie='SN-5').status_code, 400)

    def test_date_acquisition_future_refusee(self):
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self._creer(numero_serie='SN-6', date_acquisition=future).status_code, 400)
