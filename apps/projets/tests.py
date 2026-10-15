from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.equipements.models import Equipement
from apps.laboratoires.models import Laboratoire
from apps.organisations.models import Organisation
from apps.utilisateurs.models import Role, Utilisateur

from .models import NiveauPriorite, Projet


class CreationProjetPuisReservationTests(APITestCase):
    def setUp(self):
        cache.clear()
        org = Organisation.objects.create(nom='Org', slug='org-projets')
        self.chercheur = Utilisateur.objects.create_user(email='cher@org.sn', password='x', nom='C', prenom='C',
                                                         role=Role.CHERCHEUR, organisation=org)
        self.labo = Laboratoire.objects.create(nom='Labo', localisation='Bât. 1', organisation=org)
        self.equipement = Equipement.objects.create(laboratoire=self.labo, nom='PCR', numero_serie='P-1')
        self.client.force_authenticate(self.chercheur)

    def test_la_creation_renvoie_l_identifiant_et_la_priorite(self):
        reponse = self.client.post('/api/projets/', {'nom': 'Thèse paludisme', 'description': ''}, format='json')
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(reponse.data['id'], Projet.objects.get().id)
        self.assertEqual(reponse.data['niveau_priorite'], NiveauPriorite.NORMALE)
        self.assertEqual(reponse.data['responsable'], self.chercheur.id)

    def test_priorite_non_modifiable_a_la_creation(self):
        self.client.post('/api/projets/', {'nom': 'Projet', 'niveau_priorite': 'CRITIQUE'}, format='json')
        self.assertEqual(Projet.objects.get().niveau_priorite, NiveauPriorite.NORMALE)

    def test_la_reservation_porte_le_projet_cree_et_sa_priorite(self):
        projet_id = self.client.post('/api/projets/', {'nom': 'Thèse paludisme'}, format='json').data['id']
        reponse = self.client.post('/api/reservations/', {
            'laboratoire': self.labo.id, 'equipements': [self.equipement.id], 'projet': projet_id,
            'date': (timezone.localdate() + timedelta(days=1)).isoformat(),
            'heure_debut': '10:00', 'heure_fin': '12:00', 'motif': 'Analyses',
        }, format='json')
        self.assertEqual(reponse.status_code, 201, reponse.data)
        self.assertEqual(reponse.data['projet'], projet_id)
        self.assertEqual(reponse.data['projet_nom'], 'Thèse paludisme')
        self.assertEqual(reponse.data['projet_priorite'], NiveauPriorite.NORMALE)
