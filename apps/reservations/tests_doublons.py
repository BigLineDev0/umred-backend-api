"""
Double envoi d'une même demande (double clic, nouvel essai après un délai
dépassé, assistant relancé) : une seule demande doit exister.
"""
from datetime import timedelta

from .models import Reservation, StatutReservation
from .tests_file_attente import Base


class DoublonsTests(Base):
    def test_double_envoi_d_un_etudiant_refuse(self):
        self.assertEqual(self.reserver(self.etudiant).status_code, 201)
        reponse = self.reserver(self.etudiant)
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("déjà une demande", str(reponse.data))
        self.assertEqual(Reservation.objects.filter(demandeur=self.etudiant).count(), 1)

    def test_chevauchement_partiel_de_sa_propre_demande_refuse(self):
        self.existante(self.etudiant, StatutReservation.EN_ATTENTE, 10, 12)
        self.assertEqual(self.reserver(self.etudiant, heure_debut='11:00', heure_fin='13:00').status_code, 400)

    def test_creneaux_bord_a_bord_acceptes(self):
        self.existante(self.etudiant, StatutReservation.EN_ATTENTE, 10, 12)
        self.assertEqual(self.reserver(self.etudiant, heure_debut='12:00', heure_fin='14:00').status_code, 201)

    def test_demande_annulee_ou_refusee_ne_bloque_pas_une_nouvelle_demande(self):
        self.existante(self.etudiant, StatutReservation.ANNULEE)
        self.existante(self.etudiant, StatutReservation.REFUSEE)
        self.assertEqual(self.reserver(self.etudiant).status_code, 201)

    def test_la_file_d_attente_entre_utilisateurs_differents_reste_possible(self):
        self.assertEqual(self.reserver(self.etudiant).status_code, 201)
        self.assertEqual(self.reserver(self.etudiant2).status_code, 201)
        self.assertEqual(Reservation.objects.filter(statut=StatutReservation.EN_ATTENTE).count(), 2)

    def test_autre_jour_accepte(self):
        self.assertEqual(self.reserver(self.etudiant).status_code, 201)
        autre_jour = (self.demain + timedelta(days=1)).isoformat()
        self.assertEqual(self.reserver(self.etudiant, date=autre_jour).status_code, 201)

    def test_verification_annonce_le_doublon_avant_confirmation(self):
        self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.post('/api/reservations/verifier/', self.payload(), format='json')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("déjà une demande", str(reponse.data))
