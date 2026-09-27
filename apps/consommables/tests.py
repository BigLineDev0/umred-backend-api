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
