from django.core.cache import cache
from rest_framework.test import APITestCase

from apps.equipements.models import Equipement, StatutEquipement
from apps.laboratoires.models import Laboratoire
from apps.utilisateurs.models import Utilisateur, Role
from .models import Maintenance, StatutMaintenance


class AnnulationMaintenanceTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.etudiant = Utilisateur.objects.create_user(email='etu@umred.sn', password='x', nom='E', prenom='E', role=Role.ETUDIANT)
        self.technicien = Utilisateur.objects.create_user(email='tech@umred.sn', password='x', nom='T', prenom='T', role=Role.TECHNICIEN)
        labo = Laboratoire.objects.create(nom='Labo', localisation='Bât. 1')
        self.equipement = Equipement.objects.create(laboratoire=labo, nom='Centrifugeuse', numero_serie='C-1')
        self.maintenance = Maintenance.creer_depuis_signalement(self.equipement, 'Ne démarre plus', self.etudiant)

    def test_etudiant_ne_peut_pas_annuler(self):
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.post(f'/api/maintenances/{self.maintenance.id}/annuler/')
        self.assertEqual(reponse.status_code, 403)
        self.equipement.refresh_from_db()
        self.assertEqual(self.equipement.statut, StatutEquipement.EN_PANNE)

    def test_technicien_peut_annuler(self):
        self.client.force_authenticate(self.technicien)
        reponse = self.client.post(f'/api/maintenances/{self.maintenance.id}/annuler/')
        self.assertEqual(reponse.status_code, 200)

    def test_maintenance_terminee_non_annulable(self):
        self.maintenance.statut = StatutMaintenance.TERMINEE
        self.maintenance.technicien = self.technicien
        self.maintenance.save()
        self.client.force_authenticate(self.technicien)
        reponse = self.client.post(f'/api/maintenances/{self.maintenance.id}/annuler/')
        self.assertEqual(reponse.status_code, 400)


class StatutEquipementTests(AnnulationMaintenanceTests):
    def test_cloture_d_une_preventive_ne_masque_pas_une_panne(self):
        from django.utils import timezone
        from .models import TypeMaintenance
        preventive = Maintenance(equipement=self.equipement, type=TypeMaintenance.PREVENTIVE,
                                 date_planifiee=timezone.now(), technicien=self.technicien).planifier()
        preventive.cloturer('RAS')
        self.equipement.refresh_from_db()
        # La panne signalée dans setUp est toujours active.
        self.assertEqual(self.equipement.statut, StatutEquipement.EN_PANNE)

    def test_derniere_maintenance_close_libere_l_equipement(self):
        self.maintenance.technicien = self.technicien
        self.maintenance.save()
        self.maintenance.demarrer()
        self.maintenance.cloturer('Pièce changée')
        self.equipement.refresh_from_db()
        self.assertEqual(self.equipement.statut, StatutEquipement.DISPONIBLE)

    def test_signalement_sur_equipement_hors_service_refuse(self):
        self.equipement.changer_statut(StatutEquipement.HORS_SERVICE)
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.post('/api/maintenances/signaler_panne/',
                                   {'equipement': self.equipement.id, 'description': 'x'}, format='json')
        self.assertEqual(reponse.status_code, 400)


class PriseEnChargeEtSignalementTests(AnnulationMaintenanceTests):
    """Régression : prise en charge refusée (400) depuis « Équipements en panne »."""

    def setUp(self):
        super().setUp()
        from datetime import timedelta
        from django.utils import timezone
        from .models import TypeMaintenance
        self.demain = timezone.now() + timedelta(days=1)
        # Préventive annulée sur le même équipement : c'est elle que la page
        # transmettait par erreur au lieu de la panne signalée.
        self.preventive_annulee = Maintenance(
            equipement=self.equipement, type=TypeMaintenance.PREVENTIVE,
            date_planifiee=self.demain, technicien=self.technicien,
        ).planifier()
        self.preventive_annulee.annuler()

    def prendre_en_charge(self, maintenance):
        self.client.force_authenticate(self.technicien)
        return self.client.post(f'/api/maintenances/{maintenance.id}/prendre_en_charge/',
                                {'date_planifiee': self.demain.isoformat()}, format='json')

    def test_la_panne_signalee_est_prise_en_charge(self):
        reponse = self.prendre_en_charge(self.maintenance)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data['statut'], StatutMaintenance.PLANIFIEE)
        self.assertEqual(reponse.data['technicien'], self.technicien.id)

    def test_une_maintenance_annulee_ne_se_prend_pas_en_charge(self):
        reponse = self.prendre_en_charge(self.preventive_annulee)
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('Seule une panne signalée', reponse.data[0])

    def test_date_d_intervention_passee_refusee(self):
        from datetime import timedelta
        from django.utils import timezone
        self.client.force_authenticate(self.technicien)
        reponse = self.client.post(f'/api/maintenances/{self.maintenance.id}/prendre_en_charge/',
                                   {'date_planifiee': (timezone.now() - timedelta(hours=2)).isoformat()}, format='json')
        self.assertEqual(reponse.status_code, 400)

    def test_un_chercheur_peut_signaler_une_panne(self):
        chercheur = Utilisateur.objects.create_user(email='cher@umred.sn', password='x', nom='C', prenom='C', role=Role.CHERCHEUR)
        autre = Equipement.objects.create(laboratoire=self.equipement.laboratoire, nom='Spectro', numero_serie='S-1')
        self.client.force_authenticate(chercheur)
        reponse = self.client.post('/api/maintenances/signaler_panne/',
                                   {'equipement': autre.id, 'description': "Écran noir"}, format='json')
        self.assertEqual(reponse.status_code, 201)
        autre.refresh_from_db()
        self.assertEqual(autre.statut, StatutEquipement.EN_PANNE)

    def test_pas_de_double_signalement(self):
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.post('/api/maintenances/signaler_panne/',
                                   {'equipement': self.equipement.id, 'description': 'Toujours en panne'}, format='json')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('déjà signalée', reponse.data[0])
