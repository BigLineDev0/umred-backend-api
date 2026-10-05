from datetime import time, timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.equipements.models import Equipement, StatutEquipement
from apps.laboratoires.models import Laboratoire
from apps.notifications.models import Notification
from apps.organisations.models import Organisation
from apps.projets.models import NiveauPriorite, Projet
from apps.utilisateurs.models import Role, StatutAcademique, Utilisateur

from .models import AlerteCreneau, Reservation, StatutReservation
from .services import marquer_terminees


class Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.org = Organisation.objects.create(nom='UCAD', slug='ucad')
        self.encadrant = self.user('prof@ucad.sn', Role.CHERCHEUR, statut_academique=StatutAcademique.PROFESSEUR)
        self.autre_chercheur = self.user('mcf@ucad.sn', Role.CHERCHEUR)
        self.etudiant = self.user('etu@ucad.sn', Role.ETUDIANT, encadrant=self.encadrant)
        self.etudiant2 = self.user('etu2@ucad.sn', Role.ETUDIANT)
        self.technicien = self.user('tech@ucad.sn', Role.TECHNICIEN)
        self.labo = Laboratoire.objects.create(nom='Labo A', localisation='Bât. 1', organisation=self.org)
        self.equipement = Equipement.objects.create(
            laboratoire=self.labo, nom='Microscope 1', numero_serie='M-1', categorie='Microscope',
        )
        self.demain = timezone.localdate() + timedelta(days=1)

    def user(self, email, role, **extra):
        return Utilisateur.objects.create_user(
            email=email, password='MotDePasse!2026', nom='Test', prenom=email.split('@')[0],
            role=role, organisation=self.org, **extra,
        )

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

    def existante(self, demandeur, statut, debut=10, fin=12, **extra):
        r = Reservation.objects.create(
            demandeur=demandeur, laboratoire=self.labo, date=extra.pop('date', self.demain),
            heure_debut=time(debut), heure_fin=time(fin), motif='TP', statut=statut, **extra,
        )
        r.equipements.set([self.equipement])
        return r


class FileAttenteTests(Base):
    def test_une_demande_en_attente_ne_bloque_pas_le_creneau(self):
        self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        reponse = self.reserver(self.etudiant2)
        self.assertEqual(reponse.status_code, 201)

    def test_concurrence_force_la_file_meme_pour_un_chercheur(self):
        self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        reponse = self.reserver(self.autre_chercheur)
        self.assertEqual(reponse.data['statut'], StatutReservation.EN_ATTENTE)

    def test_sans_concurrence_le_chercheur_est_confirme(self):
        self.assertEqual(self.reserver(self.autre_chercheur).data['statut'], StatutReservation.VALIDEE)

    def test_valider_refuse_les_concurrentes_et_les_inscrit_en_liste_d_attente(self):
        retenue = self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        ecartee = self.existante(self.etudiant2, StatutReservation.EN_ATTENTE, debut=11, fin=13)

        self.client.force_authenticate(self.technicien)
        self.assertEqual(self.client.post(f'/api/reservations/{retenue.id}/valider/').status_code, 200)

        ecartee.refresh_from_db()
        self.assertEqual(ecartee.statut, StatutReservation.REFUSEE)
        self.assertTrue(ecartee.motif_refus)
        self.assertTrue(Notification.objects.filter(destinataire=self.etudiant2, titre='Réservation non retenue').exists())
        self.assertTrue(AlerteCreneau.objects.filter(utilisateur=self.etudiant2, active=True).exists())

    def test_validation_impossible_si_le_creneau_est_deja_attribue(self):
        self.existante(self.autre_chercheur, StatutReservation.VALIDEE)
        demande = self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        self.client.force_authenticate(self.technicien)
        self.assertEqual(self.client.post(f'/api/reservations/{demande.id}/valider/').status_code, 400)

    def test_validation_impossible_si_le_creneau_est_passe(self):
        demande = self.existante(self.etudiant, StatutReservation.EN_ATTENTE,
                                 date=timezone.localdate() - timedelta(days=1))
        self.client.force_authenticate(self.technicien)
        self.assertEqual(self.client.post(f'/api/reservations/{demande.id}/valider/').status_code, 400)

    def test_file_attente_classe_par_priorite_et_recommande(self):
        projet = Projet.objects.create(nom='P', responsable=self.encadrant, niveau_priorite=NiveauPriorite.CRITIQUE)
        normale = self.existante(self.etudiant2, StatutReservation.EN_ATTENTE)
        prioritaire = self.existante(self.etudiant, StatutReservation.EN_ATTENTE, projet=projet)

        self.client.force_authenticate(self.technicien)
        file = {r['id']: r['analyse'] for r in self.client.get('/api/reservations/file_attente/').data}

        self.assertEqual(file[prioritaire.id]['rang'], 1)
        self.assertEqual(file[prioritaire.id]['recommandation'], 'valider')
        self.assertEqual(file[normale.id]['rang'], 2)
        self.assertEqual(file[normale.id]['recommandation'], 'arbitrer')


class EncadrantTests(Base):
    def test_l_encadrant_valide_la_demande_de_son_etudiant(self):
        demande = self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        self.client.force_authenticate(self.encadrant)
        self.assertEqual(self.client.post(f'/api/reservations/{demande.id}/valider/').status_code, 200)

    def test_un_autre_chercheur_ne_peut_pas_valider(self):
        demande = self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        self.client.force_authenticate(self.autre_chercheur)
        self.assertEqual(self.client.post(f'/api/reservations/{demande.id}/valider/').status_code, 400)

    def test_equipement_sensible_releve_du_technicien(self):
        self.equipement.necessite_validation = True
        self.equipement.save()
        demande = self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        self.client.force_authenticate(self.encadrant)
        self.assertEqual(self.client.post(f'/api/reservations/{demande.id}/valider/').status_code, 400)

    def test_seul_l_encadrant_est_notifie(self):
        self.reserver(self.etudiant)
        self.assertTrue(Notification.objects.filter(destinataire=self.encadrant).exists())
        self.assertFalse(Notification.objects.filter(destinataire=self.technicien).exists())

    def test_sans_encadrant_les_techniciens_sont_notifies(self):
        self.reserver(self.etudiant2)
        self.assertTrue(Notification.objects.filter(destinataire=self.technicien).exists())

    def test_file_de_l_encadrant_limitee_a_ses_etudiants(self):
        self.existante(self.etudiant, StatutReservation.EN_ATTENTE)
        self.existante(self.etudiant2, StatutReservation.EN_ATTENTE, debut=14, fin=16)
        self.client.force_authenticate(self.encadrant)
        file = self.client.get('/api/reservations/file_attente/').data
        self.assertEqual([r['demandeur'] for r in file], [self.etudiant.id])


class DureeEtVerificationTests(Base):
    def test_duree_minimale_de_30_minutes(self):
        reponse = self.reserver(self.autre_chercheur, heure_debut='10:00', heure_fin='10:05')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('30 minutes', str(reponse.data))

    def test_duree_configurable_par_l_etablissement(self):
        self.org.duree_min_reservation = 60
        self.org.save()
        self.assertEqual(self.reserver(self.autre_chercheur, heure_debut='10:00', heure_fin='10:45').status_code, 400)

    def test_reservation_trop_lointaine_refusee(self):
        loin = (timezone.localdate() + timedelta(days=90)).isoformat()
        self.assertEqual(self.reserver(self.autre_chercheur, date=loin).status_code, 400)

    def test_verifier_annonce_le_statut_et_la_file(self):
        self.existante(self.etudiant2, StatutReservation.EN_ATTENTE)
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.post('/api/reservations/verifier/', self.payload(), format='json')
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(reponse.data['disponible'])
        self.assertEqual(reponse.data['statut_prevu'], StatutReservation.EN_ATTENTE)
        self.assertEqual(reponse.data['file_attente']['nombre'], 1)
        self.assertIn('encadrant', reponse.data['raison_statut'])
        self.assertFalse(Reservation.objects.filter(demandeur=self.etudiant).exists())

    def test_alternative_disponible_a_partir_de_la_fin_du_conflit(self):
        # 10h-11h est pris ; on demande 10h-12h -> « disponible à partir de 11h00 ».
        self.existante(self.autre_chercheur, StatutReservation.VALIDEE, debut=10, fin=11)
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.post('/api/reservations/verifier/', self.payload(), format='json')
        self.assertFalse(reponse.data['disponible'])
        premiere = reponse.data['alternatives']['creneaux'][0]
        self.assertEqual((premiere['heure_debut'], premiere['heure_fin']), ('11:00', '13:00'))
        self.assertIn('11h00', premiere['message'])

    def test_equivalent_en_panne_jamais_propose(self):
        Equipement.objects.create(laboratoire=self.labo, nom='Microscope 2', numero_serie='M-2',
                                  categorie='Microscope', statut=StatutEquipement.EN_PANNE)
        libre = Equipement.objects.create(laboratoire=self.labo, nom='Microscope 3', numero_serie='M-3',
                                          categorie='Microscope')
        self.existante(self.autre_chercheur, StatutReservation.VALIDEE)
        reponse = self.reserver(self.etudiant)
        self.assertEqual(reponse.status_code, 409)
        equivalents = reponse.data['alternatives']['equipements_equivalents']
        self.assertEqual([e['id'] for e in equivalents], [libre.id])
        self.assertEqual(equivalents[0]['remplace'], self.equipement.id)


class LiberationCreneauTests(Base):
    def test_annulation_previent_la_liste_d_attente(self):
        validee = self.existante(self.autre_chercheur, StatutReservation.VALIDEE)
        self.client.force_authenticate(self.etudiant)
        self.client.post('/api/reservations/alertes/', {
            'laboratoire': self.labo.id, 'equipements': [self.equipement.id],
            'date': self.demain.isoformat(), 'heure_debut': '10:00', 'heure_fin': '12:00',
        }, format='json')

        self.client.force_authenticate(self.autre_chercheur)
        self.assertEqual(self.client.post(f'/api/reservations/{validee.id}/annuler/').status_code, 200)

        self.assertTrue(Notification.objects.filter(destinataire=self.etudiant, titre__icontains='libéré').exists())
        self.assertFalse(AlerteCreneau.objects.filter(utilisateur=self.etudiant, active=True).exists())

    def test_on_n_annule_pas_la_reservation_d_un_autre(self):
        validee = self.existante(self.etudiant, StatutReservation.VALIDEE)
        self.client.force_authenticate(self.encadrant)
        self.assertEqual(self.client.post(f'/api/reservations/{validee.id}/annuler/').status_code, 403)

    def test_cloture_automatique(self):
        hier = timezone.localdate() - timedelta(days=1)
        passee = self.existante(self.autre_chercheur, StatutReservation.VALIDEE, date=hier)
        oubliee = self.existante(self.etudiant, StatutReservation.EN_ATTENTE, date=hier, debut=14, fin=15)
        marquer_terminees()
        passee.refresh_from_db()
        oubliee.refresh_from_db()
        self.assertEqual(passee.statut, StatutReservation.TERMINEE)
        self.assertEqual(oubliee.statut, StatutReservation.REFUSEE)


class IsolationOrganisationsTests(Base):
    def setUp(self):
        super().setUp()
        self.autre_org = Organisation.objects.create(nom='UGB', slug='ugb')
        self.labo_ugb = Laboratoire.objects.create(nom='Labo UGB', localisation='Saint-Louis', organisation=self.autre_org)
        self.equip_ugb = Equipement.objects.create(laboratoire=self.labo_ugb, nom='Spectro', numero_serie='S-1')

    def test_un_utilisateur_ne_voit_que_son_etablissement(self):
        self.client.force_authenticate(self.technicien)
        noms = [l['nom'] for l in self.client.get('/api/laboratoires/').data]
        self.assertEqual(noms, ['Labo A'])
        self.assertEqual(self.client.get(f'/api/equipements/{self.equip_ugb.id}/').status_code, 404)

    def test_impossible_de_reserver_dans_un_autre_etablissement(self):
        reponse = self.reserver(self.autre_chercheur, laboratoire=self.labo_ugb.id, equipements=[self.equip_ugb.id])
        self.assertEqual(reponse.status_code, 400)

    def test_console_super_admin(self):
        editeur = Utilisateur.objects.create_user(
            email='editeur@saas.sn', password='x', nom='E', prenom='E', role=Role.SUPER_ADMIN,
        )
        admin = self.user('admin@ucad.sn', Role.ADMIN)
        self.client.force_authenticate(admin)
        self.assertEqual(self.client.get('/api/organisations/').status_code, 403)

        self.client.force_authenticate(editeur)
        reponse = self.client.get('/api/organisations/')
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(len(reponse.data), 2)
        # L'éditeur pilote la plateforme sans lire les données des établissements.
        self.assertEqual(self.client.get('/api/laboratoires/').data, [])

    def test_admin_configure_son_etablissement(self):
        admin = self.user('admin@ucad.sn', Role.ADMIN)
        self.client.force_authenticate(admin)
        reponse = self.client.patch('/api/organisations/courante/', {'couleur_primaire': '#008751'}, format='json')
        self.assertEqual(reponse.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.couleur_primaire, '#008751')

        self.client.force_authenticate(self.etudiant)
        self.assertEqual(self.client.patch('/api/organisations/courante/', {'nom': 'X'}, format='json').status_code, 403)

    def test_organisation_suspendue_ne_peut_plus_se_connecter(self):
        self.org.est_active = False
        self.org.save()
        reponse = self.client.post('/api/auth/login/', {'email': 'tech@ucad.sn', 'password': 'MotDePasse!2026'}, format='json')
        self.assertEqual(reponse.status_code, 401)


class PilotageTests(Base):
    def test_statistiques_equipement(self):
        self.existante(self.autre_chercheur, StatutReservation.TERMINEE, date=timezone.localdate() - timedelta(days=3))
        self.client.force_authenticate(self.technicien)
        reponse = self.client.get(f'/api/equipements/{self.equipement.id}/statistiques/')
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data['usage']['heures_totales'], 2.0)
        self.assertIn('maintenance_estimee', reponse.data['prevision'])
        self.assertEqual(self.client.get('/api/equipements/').data[0]['nombre_utilisations'], 1)

    def test_panne_previent_les_reservations_impactees(self):
        self.existante(self.etudiant, StatutReservation.VALIDEE)
        self.client.force_authenticate(self.autre_chercheur)
        reponse = self.client.post('/api/maintenances/signaler_panne/', {
            'equipement': self.equipement.id, 'description': 'Ne démarre plus',
        }, format='json')
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(reponse.data['reservations_impactees'], 1)
        self.assertTrue(Notification.objects.filter(destinataire=self.etudiant, titre__icontains='indisponible').exists())

    def test_export_pdf_et_indicateurs(self):
        self.existante(self.autre_chercheur, StatutReservation.TERMINEE, date=timezone.localdate() - timedelta(days=3))
        admin = self.user('admin@ucad.sn', Role.ADMIN)
        self.client.force_authenticate(admin)
        pdf = self.client.get('/api/rapports/export-pdf/')
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))

        indicateurs = self.client.get('/api/pilotage/indicateurs/')
        self.assertEqual(indicateurs.status_code, 200)
        self.assertIn('recommandations', indicateurs.data)
        self.client.force_authenticate(self.etudiant)
        self.assertEqual(self.client.get('/api/rapports/export-pdf/').status_code, 403)


class CoherenceTests(Base):
    def test_on_n_annule_pas_une_reservation_passee(self):
        passee = self.existante(self.etudiant, StatutReservation.VALIDEE, date=timezone.localdate() - timedelta(days=1))
        self.client.force_authenticate(self.etudiant)
        self.assertEqual(self.client.post(f'/api/reservations/{passee.id}/annuler/').status_code, 400)
        self.assertFalse(self.client.get(f'/api/reservations/{passee.id}/').data['annulable'])

    def test_preventive_planifiee_n_immobilise_pas_l_equipement(self):
        from apps.maintenances.models import Maintenance, TypeMaintenance
        from datetime import datetime
        jour = timezone.localdate() + timedelta(days=5)
        Maintenance(equipement=self.equipement, type=TypeMaintenance.PREVENTIVE, technicien=self.technicien,
                    date_planifiee=timezone.make_aware(datetime.combine(jour, time(9)))).planifier()
        self.equipement.refresh_from_db()
        self.assertEqual(self.equipement.statut, StatutEquipement.DISPONIBLE)
        # Réservable demain, mais pas le jour de l'intervention.
        self.assertEqual(self.reserver(self.autre_chercheur).status_code, 201)
        self.assertEqual(self.reserver(self.autre_chercheur, date=jour.isoformat()).status_code, 400)

    def test_demarrer_une_preventive_immobilise_l_equipement(self):
        from apps.maintenances.models import Maintenance, TypeMaintenance
        m = Maintenance(equipement=self.equipement, type=TypeMaintenance.PREVENTIVE, technicien=self.technicien,
                        date_planifiee=timezone.now()).planifier()
        m.demarrer()
        self.equipement.refresh_from_db()
        self.assertEqual(self.equipement.statut, StatutEquipement.EN_MAINTENANCE)

    def test_desactivation_annule_les_reservations_futures(self):
        from django.core import mail
        admin = self.user('admin@ucad.sn', Role.ADMIN)
        future = self.existante(self.autre_chercheur, StatutReservation.VALIDEE)
        AlerteCreneau.objects.create(utilisateur=self.etudiant, laboratoire=self.labo, date=self.demain,
                                     heure_debut=time(10), heure_fin=time(12)).equipements.set([self.equipement])
        self.client.force_authenticate(admin)
        self.client.post(f'/api/utilisateurs/{self.autre_chercheur.id}/desactiver/')
        future.refresh_from_db()
        self.assertEqual(future.statut, StatutReservation.ANNULEE)
        # La personne en liste d'attente est prévenue, y compris par email.
        self.assertTrue(any(self.etudiant.email in m.to for m in mail.outbox))

    def test_hors_service_previent_les_reservations(self):
        self.existante(self.etudiant, StatutReservation.VALIDEE)
        self.client.force_authenticate(self.technicien)
        self.client.patch(f'/api/equipements/{self.equipement.id}/', {'statut': 'HORS_SERVICE'}, format='json')
        self.assertTrue(Notification.objects.filter(destinataire=self.etudiant, titre__icontains='indisponible').exists())


class SyntheseHebdomadaireTests(Base):
    def test_synthese_envoyee_meme_si_le_service_ia_est_injoignable(self):
        from django.core import mail
        from django.core.management import call_command
        from django.test import override_settings
        admin = self.user('admin@ucad.sn', Role.ADMIN)
        with override_settings(IA_SERVICE_URL='http://127.0.0.1:9/api'):
            call_command('envoyer_synthese_hebdomadaire')
        notification = Notification.objects.get(destinataire=admin)
        self.assertIn('réservations ont été enregistrées', notification.message)
        self.assertTrue(any(admin.email in m.to for m in mail.outbox))


class ProjetEncadrantTests(Base):
    def test_etudiant_rattache_sa_demande_au_projet_de_son_encadrant(self):
        projet = Projet.objects.create(nom='Thèse', responsable=self.encadrant, niveau_priorite=NiveauPriorite.HAUTE)
        self.client.force_authenticate(self.etudiant)
        self.assertEqual([p['id'] for p in self.client.get('/api/projets/').data], [projet.id])
        self.assertEqual(self.reserver(self.etudiant, projet=projet.id).status_code, 201)
        # Un étudiant d'un autre encadrant ne le peut pas.
        self.assertEqual(self.reserver(self.etudiant2, projet=projet.id).status_code, 400)

    def test_alerte_sur_un_creneau_passe_refusee(self):
        self.client.force_authenticate(self.etudiant)
        reponse = self.client.post('/api/reservations/alertes/', {
            'laboratoire': self.labo.id, 'equipements': [self.equipement.id],
            'date': (timezone.localdate() - timedelta(days=1)).isoformat(), 'heure_debut': '10:00', 'heure_fin': '12:00',
        }, format='json')
        self.assertEqual(reponse.status_code, 400)


class TachesPlanifieesTests(Base):
    def test_jeton_obligatoire(self):
        from django.test import override_settings
        url = '/api/taches/cloturer-reservations/'
        with override_settings(TACHES_TOKEN=''):
            self.assertEqual(self.client.post(url, HTTP_X_TACHES_TOKEN='').status_code, 403)
        with override_settings(TACHES_TOKEN='secret-de-test'):
            self.assertEqual(self.client.post(url, HTTP_X_TACHES_TOKEN='mauvais').status_code, 403)
            passee = self.existante(self.autre_chercheur, StatutReservation.VALIDEE,
                                    date=timezone.localdate() - timedelta(days=1))
            reponse = self.client.post(url, HTTP_X_TACHES_TOKEN='secret-de-test')
            self.assertEqual(reponse.status_code, 200)
            passee.refresh_from_db()
            self.assertEqual(passee.statut, StatutReservation.TERMINEE)

    def test_synthese_par_n8n(self):
        from django.test import override_settings
        self.user('admin@ucad.sn', Role.ADMIN)
        with override_settings(TACHES_TOKEN='secret-de-test', IA_SERVICE_URL='http://127.0.0.1:9/api'):
            reponse = self.client.post('/api/taches/synthese-hebdomadaire/', HTTP_X_TACHES_TOKEN='secret-de-test')
        self.assertEqual(reponse.data['syntheses_envoyees'], 1)
