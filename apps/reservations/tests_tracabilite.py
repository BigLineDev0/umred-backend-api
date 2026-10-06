"""
Traçabilité et visibilité des réservations traitées : qui a validé, listes
« traitées par moi » et archives, chronologie issue du journal d'audit.
"""
from datetime import time, timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.equipements.models import Equipement
from apps.laboratoires.models import Laboratoire
from apps.utilisateurs.models import Role
from .models import Reservation, StatutReservation
from .tests import creer_utilisateur


class TracabiliteTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = creer_utilisateur('admin@senlab.sn', Role.ADMIN)
        self.technicien = creer_utilisateur('tech@senlab.sn', Role.TECHNICIEN)
        self.technicien2 = creer_utilisateur('tech2@senlab.sn', Role.TECHNICIEN)
        self.etudiant = creer_utilisateur('etu@senlab.sn', Role.ETUDIANT)
        self.labo = Laboratoire.objects.create(nom='Labo A', localisation='Bât. 1')
        self.equipement = Equipement.objects.create(laboratoire=self.labo, nom='Microscope', numero_serie='M-1')
        self.demain = timezone.localdate() + timedelta(days=1)

    def creer(self, demandeur, statut=StatutReservation.EN_ATTENTE, heure=10):
        r = Reservation.objects.create(
            demandeur=demandeur, laboratoire=self.labo, date=self.demain,
            heure_debut=time(heure), heure_fin=time(heure + 1), motif='TP', statut=statut,
        )
        r.equipements.set([self.equipement])
        return r

    def ids(self, reponse):
        return {r['id'] for r in reponse.data}

    # --- Qui a validé ---

    def test_validation_expose_le_validateur(self):
        r = self.creer(self.etudiant)
        self.client.force_authenticate(self.technicien)
        reponse = self.client.post(f'/api/reservations/{r.id}/valider/')
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data['validateur_nom'], self.technicien.nom_complet)
        self.assertFalse(reponse.data['decision_automatique'])

    def test_refus_concurrence_marque_automatique(self):
        retenue = self.creer(self.etudiant)
        autre = creer_utilisateur('etu2@senlab.sn', Role.ETUDIANT)
        ecartee = self.creer(autre)
        self.client.force_authenticate(self.technicien)
        self.client.post(f'/api/reservations/{retenue.id}/valider/')
        reponse = self.client.get(f'/api/reservations/{ecartee.id}/')
        self.assertEqual(reponse.data['statut'], StatutReservation.REFUSEE)
        self.assertTrue(reponse.data['decision_automatique'])

    # --- Listes ---

    def test_traitees_ne_montre_que_mes_decisions(self):
        mienne = self.creer(self.etudiant, heure=9)
        sienne = self.creer(self.etudiant, heure=14)
        self.client.force_authenticate(self.technicien)
        self.client.post(f'/api/reservations/{mienne.id}/valider/')
        self.client.force_authenticate(self.technicien2)
        self.client.post(f'/api/reservations/{sienne.id}/refuser/', {'motif': 'TP'})

        self.client.force_authenticate(self.technicien)
        reponse = self.client.get('/api/reservations/', {'traitees': 'true'})
        self.assertEqual(self.ids(reponse), {mienne.id})

    def test_vue_globale_reservee_a_l_admin(self):
        self.creer(self.etudiant)
        self.client.force_authenticate(self.technicien)
        self.assertEqual(self.client.get('/api/reservations/', {'all': 'true'}).status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(len(self.client.get('/api/reservations/', {'all': 'true'}).data), 1)

    def test_file_attente_exclut_ses_propres_demandes(self):
        self.creer(self.technicien)
        self.client.force_authenticate(self.technicien)
        self.assertEqual(self.client.get('/api/reservations/file_attente/').data, [])
        # Elle reste visible dans « Mes réservations ».
        self.assertEqual(len(self.client.get('/api/reservations/').data), 1)

    # --- Archives ---

    def test_archivage_deplace_vers_les_archives(self):
        r = self.creer(self.etudiant, StatutReservation.ANNULEE)
        self.client.force_authenticate(self.admin)
        reponse = self.client.post(f'/api/reservations/{r.id}/archiver/')
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data['archivee_par_nom'], self.admin.nom_complet)

        self.assertNotIn(r.id, self.ids(self.client.get('/api/reservations/', {'all': 'true'})))
        self.assertEqual(self.ids(self.client.get('/api/reservations/', {'all': 'true', 'archivees': 'only'})), {r.id})

        self.client.post(f'/api/reservations/{r.id}/desarchiver/')
        self.assertIn(r.id, self.ids(self.client.get('/api/reservations/', {'all': 'true'})))

    def test_archivage_refuse_si_issue_non_definitive(self):
        for statut in [StatutReservation.EN_ATTENTE, StatutReservation.VALIDEE]:
            r = self.creer(self.etudiant, statut)
            self.client.force_authenticate(self.admin)
            self.assertEqual(self.client.post(f'/api/reservations/{r.id}/archiver/').status_code, 400)
            r.delete()

    def test_archives_reservees_a_l_admin(self):
        self.client.force_authenticate(self.technicien)
        reponse = self.client.get('/api/reservations/', {'traitees': 'true', 'archivees': 'only'})
        self.assertEqual(reponse.status_code, 403)

    # --- Annulation et historique ---

    def test_annulation_trace_son_auteur(self):
        r = self.creer(self.etudiant, StatutReservation.VALIDEE)
        self.client.force_authenticate(self.technicien)
        reponse = self.client.post(f'/api/reservations/{r.id}/annuler/')
        self.assertEqual(reponse.data['annulee_par_nom'], self.technicien.nom_complet)
        self.assertIsNotNone(reponse.data['date_annulation'])

    def test_historique_chronologique(self):
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.post('/api/reservations/', {
            'laboratoire': self.labo.id, 'equipements': [self.equipement.id],
            'date': self.demain.isoformat(), 'heure_debut': '10:00', 'heure_fin': '12:00', 'motif': 'TP',
        }, format='json')
        rid = reponse.data['id']
        self.client.force_authenticate(self.technicien)
        self.client.post(f'/api/reservations/{rid}/valider/')

        self.client.force_authenticate(self.etudiant)
        historique = self.client.get(f'/api/reservations/{rid}/historique/').data
        self.assertEqual([e['action'] for e in historique], ['Création de réservation', 'Validation de réservation'])
        self.assertEqual(historique[1]['auteur'], self.technicien.nom_complet)

    def test_historique_d_autrui_invisible_pour_un_etudiant(self):
        r = self.creer(creer_utilisateur('autre@senlab.sn', Role.ETUDIANT))
        self.client.force_authenticate(self.etudiant)
        self.assertEqual(self.client.get(f'/api/reservations/{r.id}/historique/').status_code, 404)
