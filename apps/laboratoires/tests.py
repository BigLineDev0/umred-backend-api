from rest_framework.test import APITestCase

from apps.utilisateurs.models import Utilisateur, Role
from .models import Laboratoire


class LaboratoireValidationTests(APITestCase):
    def setUp(self):
        self.admin = Utilisateur.objects.create_user(
            email='admin@test.sn', password='x', nom='A', prenom='A', role=Role.ADMIN
        )
        self.client.force_authenticate(self.admin)
        Laboratoire.objects.create(nom='Labo Bio', localisation='Bât. 1', description='Laboratoire de biologie')

    def _creer(self, **kw):
        data = {'nom': 'Labo Chimie', 'localisation': 'Bât. 2',
                'description': 'Laboratoire de chimie générale', 'statut': 'DISPONIBLE'}
        data.update(kw)
        return self.client.post('/api/laboratoires/', data, format='json')

    def test_creation_valide(self):
        self.assertEqual(self._creer().status_code, 201)

    def test_doublon_insensible_casse_et_espaces(self):
        reponse = self._creer(nom='  labo   BIO ')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('nom', reponse.json())

    def test_nom_invalide(self):
        for mauvais in ['<b>x</b>', '123', '----', 'a']:
            self.assertEqual(self._creer(nom=mauvais).status_code, 400, msg=mauvais)

    def test_description_obligatoire_min(self):
        self.assertEqual(self._creer(description='court').status_code, 400)
