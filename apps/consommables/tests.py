from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.laboratoires.models import Laboratoire
from apps.utilisateurs.models import Utilisateur, Role
from .models import Consommable, MouvementStock


class StockTests(TestCase):
    def setUp(self):
        self.user = Utilisateur.objects.create_user(email='u@umred.sn', password='x', nom='U', prenom='U', role=Role.CHERCHEUR)
        labo = Laboratoire.objects.create(nom='Labo', localisation='Bât. 1')
        self.consommable = Consommable.objects.create(laboratoire=labo, nom='Éthanol', quantite_stock=Decimal('10'))

    def test_retrait_relit_le_stock_en_base(self):
        # Simule un retrait fait par quelqu'un d'autre entre-temps : l'objet
        # en mémoire est périmé, le retrait doit partir de la valeur en base.
        copie_perimee = Consommable.objects.get(pk=self.consommable.pk)
        self.consommable.retirer_stock(Decimal('8'), self.user)

        with self.assertRaises(ValidationError):
            copie_perimee.retirer_stock(Decimal('5'), self.user)

        self.consommable.refresh_from_db()
        self.assertEqual(self.consommable.quantite_stock, Decimal('2'))
        self.assertEqual(MouvementStock.objects.count(), 1)


class AjustementEtAlertesTests(StockTests):
    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        from apps.notifications.models import Notification
        self.Notification = Notification
        self.technicien = Utilisateur.objects.create_user(email='t@umred.sn', password='x', nom='T', prenom='T', role=Role.TECHNICIEN)
        self.api = APIClient()

    def test_modification_du_stock_tracee(self):
        from .models import TypeMouvement
        self.api.force_authenticate(self.technicien)
        reponse = self.api.patch(f'/api/consommables/{self.consommable.id}/', {'quantite_stock': '7'}, format='json')
        self.assertEqual(reponse.status_code, 200)
        mouvement = MouvementStock.objects.get()
        self.assertEqual(mouvement.type, TypeMouvement.AJUSTEMENT)
        self.assertEqual(mouvement.quantite, Decimal('-3'))

    def test_alerte_envoyee_une_seule_fois(self):
        self.consommable.seuil_alerte = Decimal('5')
        self.consommable.save()
        self.api.force_authenticate(self.user)
        url = f'/api/consommables/{self.consommable.id}/retirer/'
        self.api.post(url, {'quantite': '6'}, format='json')  # 10 -> 4 : passe sous le seuil
        self.api.post(url, {'quantite': '1'}, format='json')  # 4 -> 3 : toujours sous le seuil
        self.assertEqual(self.Notification.objects.filter(destinataire=self.technicien).count(), 1)


from rest_framework.test import APITestCase  # noqa: E402


class ConsommableValidationTests(APITestCase):
    def setUp(self):
        self.tech = Utilisateur.objects.create_user(
            email='conso@test.sn', password='x', nom='C', prenom='C', role=Role.TECHNICIEN)
        self.labo = Laboratoire.objects.create(nom='Labo conso', localisation='Bât. 1')
        Consommable.objects.create(laboratoire=self.labo, nom='Éthanol', reference='ETH-001',
                                   quantite_stock=Decimal('10'))
        self.client.force_authenticate(self.tech)

    def _creer(self, **kw):
        data = {'laboratoire': self.labo.id, 'nom': 'Acétone', 'reference': 'ACE-001',
                'unite': 'UNITE', 'quantite_stock': '5', 'seuil_alerte': '1'}
        data.update(kw)
        return self.client.post('/api/consommables/', data, format='json')

    def test_creation_valide_met_reference_en_majuscules(self):
        reponse = self._creer(reference='ace-001')
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(reponse.json()['reference'], 'ACE-001')

    def test_nom_doublon_insensible_casse_espaces(self):
        reponse = self._creer(nom='  ethanol ', reference='X-1')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('nom', reponse.json())

    def test_reference_doublon(self):
        reponse = self._creer(nom='Autre produit', reference='eth-001')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('reference', reponse.json())

    def test_reference_format_invalide(self):
        self.assertEqual(self._creer(reference='a b').status_code, 400)

    def test_quantite_negative_refusee(self):
        self.assertEqual(self._creer(reference='Q-1', quantite_stock='-1').status_code, 400)
