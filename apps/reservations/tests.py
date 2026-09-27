from datetime import time, timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.equipements.models import Equipement, StatutEquipement
from apps.laboratoires.models import Laboratoire, StatutLaboratoire
from apps.utilisateurs.models import Utilisateur, Role
from .models import Reservation, StatutReservation


def creer_utilisateur(email, role):
    return Utilisateur.objects.create_user(email=email, password='MotDePasse!2026', nom='Test', prenom=email, role=role)


class ReservationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.etudiant = creer_utilisateur('etu@umred.sn', Role.ETUDIANT)
        self.chercheur = creer_utilisateur('cher@umred.sn', Role.CHERCHEUR)
        self.technicien = creer_utilisateur('tech@umred.sn', Role.TECHNICIEN)
        self.labo = Laboratoire.objects.create(nom='Labo A', localisation='Bât. 1')
        self.equipement = Equipement.objects.create(laboratoire=self.labo, nom='Microscope', numero_serie='M-1')
        self.demain = timezone.localdate() + timedelta(days=1)

    def payload(self, **extra):
        data = {
            'laboratoire': self.labo.id, 'equipements': [self.equipement.id],
            'date': self.demain.isoformat(), 'heure_debut': '10:00', 'heure_fin': '12:00', 'motif': 'TP',
        }
        data.update(extra)
        return data

    def reserver(self, user, **extra):
        self.client.force_authenticate(user)
        return self.client.post('/api/reservations/', self.payload(**extra), format='json')

    def creer_reservation(self, demandeur, statut=StatutReservation.EN_ATTENTE):
        r = Reservation.objects.create(
            demandeur=demandeur, laboratoire=self.labo, date=self.demain,
            heure_debut=time(10), heure_fin=time(12), motif='TP', statut=statut,
        )
        r.equipements.set([self.equipement])
        return r

    # --- Création ---

    def test_etudiant_creation_en_attente(self):
        reponse = self.reserver(self.etudiant)
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(reponse.data['statut'], StatutReservation.EN_ATTENTE)

    def test_conflit_renvoie_409(self):
        self.creer_reservation(self.chercheur, StatutReservation.VALIDEE)
        self.assertEqual(self.reserver(self.etudiant).status_code, 409)

    def test_equipement_en_panne_non_reservable(self):
        self.equipement.changer_statut(StatutEquipement.EN_PANNE)
        self.assertEqual(self.reserver(self.chercheur).status_code, 400)
        self.assertFalse(Reservation.objects.exists())

    def test_laboratoire_indisponible_non_reservable(self):
        self.labo.statut = StatutLaboratoire.INDISPONIBLE
        self.labo.save()
        self.assertEqual(self.reserver(self.chercheur).status_code, 400)

    def test_date_passee_refusee(self):
        hier = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.assertEqual(self.reserver(self.chercheur, date=hier).status_code, 400)

    def test_hors_heures_ouverture_refusee(self):
        self.assertEqual(self.reserver(self.chercheur, heure_debut='18:00', heure_fin='20:00').status_code, 400)

    # --- Modification ---

    def test_modification_interdite(self):
        r = self.creer_reservation(self.etudiant, StatutReservation.VALIDEE)
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.patch(f'/api/reservations/{r.id}/', {'heure_fin': '17:00'}, format='json')
        self.assertEqual(reponse.status_code, 405)

    # --- Validation / refus / annulation ---

    def test_validation_par_technicien(self):
        r = self.creer_reservation(self.etudiant)
        self.client.force_authenticate(self.technicien)
        self.assertEqual(self.client.post(f'/api/reservations/{r.id}/valider/').status_code, 200)
        r.refresh_from_db()
        self.assertEqual(r.statut, StatutReservation.VALIDEE)

    def test_pas_de_validation_de_sa_propre_demande(self):
        r = self.creer_reservation(self.chercheur)
        self.client.force_authenticate(self.chercheur)
        self.assertEqual(self.client.post(f'/api/reservations/{r.id}/valider/').status_code, 400)

    def test_pas_de_validation_d_une_reservation_annulee(self):
        r = self.creer_reservation(self.etudiant, StatutReservation.ANNULEE)
        self.client.force_authenticate(self.technicien)
        self.assertEqual(self.client.post(f'/api/reservations/{r.id}/valider/').status_code, 400)
        r.refresh_from_db()
        self.assertEqual(r.statut, StatutReservation.ANNULEE)

    def test_pas_d_annulation_d_une_reservation_refusee(self):
        r = self.creer_reservation(self.etudiant, StatutReservation.REFUSEE)
        self.client.force_authenticate(self.etudiant)
        self.assertEqual(self.client.post(f'/api/reservations/{r.id}/annuler/').status_code, 400)


class ConfidentialiteEtProjetTests(ReservationTests):
    def test_planning_labo_masque_les_donnees_privees(self):
        self.creer_reservation(self.chercheur, StatutReservation.VALIDEE)
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.get(f'/api/reservations/?laboratoire={self.labo.id}')
        self.assertEqual(reponse.status_code, 200)
        ligne = reponse.data[0]
        self.assertIn('demandeur_nom', ligne)
        for champ in ['demandeur_email', 'motif', 'projet']:
            self.assertNotIn(champ, ligne)

    def test_superviseur_voit_tout(self):
        self.creer_reservation(self.etudiant, StatutReservation.VALIDEE)
        self.client.force_authenticate(self.technicien)
        reponse = self.client.get(f'/api/reservations/?laboratoire={self.labo.id}')
        self.assertIn('demandeur_email', reponse.data[0])

    def test_projet_d_un_autre_refuse(self):
        from apps.projets.models import Projet, NiveauPriorite
        projet = Projet.objects.create(nom='Critique', responsable=self.chercheur, niveau_priorite=NiveauPriorite.CRITIQUE)
        self.assertEqual(self.reserver(self.etudiant, projet=projet.id).status_code, 400)
        self.assertEqual(self.reserver(self.chercheur, projet=projet.id).status_code, 201)

    def test_rappels_reserves_a_l_admin(self):
        self.client.force_authenticate(self.technicien)
        self.assertEqual(self.client.get('/api/reservations/a_rappeler_24h/').status_code, 403)

    def test_horaires_incoherents_400(self):
        self.assertEqual(self.reserver(self.chercheur, heure_debut='12:00', heure_fin='10:00').status_code, 400)

    def test_date_invalide_400(self):
        self.client.force_authenticate(self.chercheur)
        self.assertEqual(self.client.get('/api/reservations/?date_debut=abc').status_code, 400)
