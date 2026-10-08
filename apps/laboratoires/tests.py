import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
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



def _image(format_image='PNG', nom='photo.png'):
    tampon = BytesIO()
    Image.new('RGB', (20, 20), (30, 60, 200)).save(tampon, format=format_image)
    return SimpleUploadedFile(nom, tampon.getvalue(), content_type=f'image/{format_image.lower()}')


# Dossier média temporaire : les tests n'écrivent pas dans le vrai volume.
MEDIA_TEST = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_TEST)
class LaboratoirePhotoTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.admin = Utilisateur.objects.create_user(
            email='photo@test.sn', password='x', nom='P', prenom='P', role=Role.ADMIN
        )
        self.client.force_authenticate(self.admin)
        self.labo = Laboratoire.objects.create(nom='Labo Photo', localisation='Bât. 1',
                                               description='Laboratoire avec photo')

    def _envoyer(self, fichier):
        return self.client.patch(f'/api/laboratoires/{self.labo.id}/', {'photo': fichier}, format='multipart')

    def test_photo_valide_acceptee(self):
        reponse = self._envoyer(_image())
        self.assertEqual(reponse.status_code, 200)
        self.assertIn('/media/photos_laboratoires/', reponse.json()['photo'])

    def test_faux_fichier_image_refuse(self):
        faux = SimpleUploadedFile('photo.png', b'<html>pas une image</html>', content_type='image/png')
        self.assertEqual(self._envoyer(faux).status_code, 400)

    def test_format_non_supporte_refuse(self):
        self.assertEqual(self._envoyer(_image('GIF', 'photo.gif')).status_code, 400)

    def test_retirer_la_photo(self):
        self._envoyer(_image())
        reponse = self.client.patch(f'/api/laboratoires/{self.labo.id}/', {'photo': None}, format='json')
        self.assertEqual(reponse.status_code, 200)
        self.assertIsNone(reponse.json()['photo'])
