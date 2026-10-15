"""
Contrat entre l'API et les calendriers des dashboards Étudiant et Chercheur
(frontend : Shared/components/reservations-calendar, Features/dashboard/
dashboards-calendrier.spec.ts). Le calendrier affiche ce que renvoient ces
routes : leurs champs, leurs formats et leurs règles de visibilité sont
vérifiés ici.
"""
import re

from .models import StatutReservation
from .tests_file_attente import Base

CHAMPS_DU_CALENDRIER = {"id", "date", "heure_debut", "heure_fin", "statut", "laboratoire_nom", "equipements_noms",
                        "demandeur_nom", "annulable"}


class ContratCalendrierTests(Base):
    def setUp(self):
        super().setUp()
        self.r_etudiant = self.existante(self.etudiant, StatutReservation.EN_ATTENTE, 10, 12)
        self.r_autre = self.existante(self.etudiant2, StatutReservation.VALIDEE, 14, 16)
        self.r_annulee = self.existante(self.etudiant, StatutReservation.ANNULEE, 8, 9)

    def lister(self, user, **params):
        self.client.force_authenticate(user)
        reponse = self.client.get('/api/reservations/', params)
        self.assertEqual(reponse.status_code, 200)
        return reponse.json()

    def test_champs_et_formats_attendus_par_le_calendrier(self):
        donnees = self.lister(self.etudiant)
        self.assertTrue(donnees)
        for r in donnees:
            self.assertTrue(CHAMPS_DU_CALENDRIER <= set(r), CHAMPS_DU_CALENDRIER - set(r))
            self.assertRegex(r["date"], r"^\d{4}-\d{2}-\d{2}$")  # date locale, sans fuseau : pas de décalage à l'affichage
            self.assertRegex(r["heure_debut"], r"^\d{2}:\d{2}:\d{2}$")
            self.assertIn(r["statut"], StatutReservation.values)
            self.assertEqual(r["equipements_noms"], ["Microscope 1"])

    def test_l_etudiant_ne_recoit_que_ses_reservations_y_compris_annulees(self):
        ids = {r["id"] for r in self.lister(self.etudiant)}
        # Les annulées sont renvoyées : le calendrier les montre en retrait, jamais comme actives.
        self.assertEqual(ids, {self.r_etudiant.id, self.r_annulee.id})

    def test_le_chercheur_recoit_les_demandes_de_ses_etudiants_a_traiter(self):
        self.client.force_authenticate(self.encadrant)
        reponse = self.client.get('/api/reservations/file_attente/')
        self.assertEqual(reponse.status_code, 200)
        demandes = {r["id"]: r for r in reponse.json()}
        self.assertEqual(set(demandes), {self.r_etudiant.id})  # pas la réservation d'un étudiant qu'il n'encadre pas
        self.assertTrue(CHAMPS_DU_CALENDRIER <= set(demandes[self.r_etudiant.id]))
        self.assertTrue(re.match(r"^etu", demandes[self.r_etudiant.id]["demandeur_nom"]))

    def test_l_etudiant_n_a_pas_acces_a_la_file_des_validateurs(self):
        self.client.force_authenticate(self.etudiant)
        self.assertEqual(self.client.get('/api/reservations/file_attente/').status_code, 403)

    def test_une_periode_ne_renvoie_que_ses_jours(self):
        jour = self.demain.isoformat()
        donnees = self.lister(self.etudiant, date_debut=jour, date_fin=jour)
        self.assertTrue(all(r["date"] == jour for r in donnees))
        self.assertEqual(self.lister(self.etudiant, date_debut="2000-01-01", date_fin="2000-01-31"), [])
