from unittest.mock import patch

from django.core.cache import cache
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Utilisateur, Role, StatutAcademique


class DesactivationCompteTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = Utilisateur.objects.create_user(email='admin@umred.sn', password='x', nom='A', prenom='A', role=Role.ADMIN)
        self.chercheur = Utilisateur.objects.create_user(email='cher@umred.sn', password='x', nom='C', prenom='C', role=Role.CHERCHEUR)

    def test_token_existant_refuse_apres_desactivation(self):
        access = str(RefreshToken.for_user(self.chercheur).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(self.client.get('/api/utilisateurs/moi/').status_code, 200)

        self.chercheur.desactiver_compte()

        self.assertEqual(self.client.get('/api/utilisateurs/moi/').status_code, 401)

    def test_reactivation_rend_l_acces(self):
        self.chercheur.desactiver_compte()
        self.chercheur.activer_compte()
        self.chercheur.refresh_from_db()
        self.assertTrue(self.chercheur.is_active)

    @patch('apps.utilisateurs.views.envoyer_lien_definition_mdp')
    def test_creation_conserve_statut_academique(self, _envoi):
        self.client.force_authenticate(self.admin)
        reponse = self.client.post('/api/utilisateurs/', {
            'nom': 'Diop', 'prenom': 'Awa', 'email': 'awa@umred.sn',
            'role': Role.CHERCHEUR, 'statut_academique': StatutAcademique.PROFESSEUR,
        }, format='json')
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(Utilisateur.objects.get(email='awa@umred.sn').statut_academique, StatutAcademique.PROFESSEUR)


class ThrottlingConnexionTests(APITestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_trop_de_tentatives_de_connexion(self):
        codes = [
            self.client.post('/api/auth/login/', {'email': 'x@umred.sn', 'password': 'faux'}, format='json').status_code
            for _ in range(11)
        ]
        self.assertEqual(codes[-1], 429)


class MotDePasseTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = Utilisateur.objects.create_user(
            email='awa@umred.sn', password='Ancien-Mdp-2026', nom='Diop', prenom='Awa', role=Role.CHERCHEUR,
        )

    def test_mot_de_passe_oublie_envoie_un_lien(self):
        from django.core import mail
        from .models import JetonDefinitionMotDePasse, MotifJeton

        reponse = self.client.post('/api/auth/mot-de-passe-oublie/', {'email': 'AWA@umred.sn'}, format='json')

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        jeton = JetonDefinitionMotDePasse.objects.get(utilisateur=self.user)
        self.assertEqual(jeton.motif, MotifJeton.REINITIALISATION)
        self.assertIn(jeton.jeton, mail.outbox[0].body)

    def test_mot_de_passe_oublie_meme_reponse_si_email_inconnu(self):
        from django.core import mail
        connu = self.client.post('/api/auth/mot-de-passe-oublie/', {'email': 'awa@umred.sn'}, format='json')
        inconnu = self.client.post('/api/auth/mot-de-passe-oublie/', {'email': 'personne@umred.sn'}, format='json')
        self.assertEqual(connu.status_code, inconnu.status_code)
        self.assertEqual(connu.data, inconnu.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_nouveau_lien_invalide_l_ancien(self):
        from .models import JetonDefinitionMotDePasse, MotifJeton
        premier = JetonDefinitionMotDePasse.generer_pour(self.user, MotifJeton.REINITIALISATION)
        JetonDefinitionMotDePasse.generer_pour(self.user, MotifJeton.REINITIALISATION)
        premier.refresh_from_db()
        self.assertFalse(premier.est_valide())

    def test_lien_de_reinitialisation_expire_apres_une_heure(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import JetonDefinitionMotDePasse, MotifJeton
        jeton = JetonDefinitionMotDePasse.generer_pour(self.user, MotifJeton.REINITIALISATION)
        JetonDefinitionMotDePasse.objects.filter(pk=jeton.pk).update(date_creation=timezone.now() - timedelta(hours=2))
        jeton.refresh_from_db()
        self.assertFalse(jeton.est_valide())

    def test_reinitialisation_revoque_les_sessions(self):
        from .models import JetonDefinitionMotDePasse, MotifJeton
        ancien_refresh = str(RefreshToken.for_user(self.user))
        jeton = JetonDefinitionMotDePasse.generer_pour(self.user, MotifJeton.REINITIALISATION)

        reponse = self.client.post('/api/auth/definir-mot-de-passe/',
                                   {'jeton': jeton.jeton, 'password': 'Nouveau-Mdp-2026'}, format='json')

        self.assertEqual(reponse.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Nouveau-Mdp-2026'))
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': ancien_refresh}, format='json').status_code, 401)

    def test_mot_de_passe_faible_refuse(self):
        from .models import JetonDefinitionMotDePasse
        jeton = JetonDefinitionMotDePasse.generer_pour(self.user)
        reponse = self.client.post('/api/auth/definir-mot-de-passe/',
                                   {'jeton': jeton.jeton, 'password': '12345678'}, format='json')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('detail', reponse.data)

    def test_changer_mot_de_passe_renvoie_de_nouveaux_tokens(self):
        ancien_refresh = str(RefreshToken.for_user(self.user))
        self.client.force_authenticate(self.user)
        reponse = self.client.post('/api/auth/changer-mot-de-passe/', {
            'ancien_password': 'Ancien-Mdp-2026', 'nouveau_password': 'Nouveau-Mdp-2026',
        }, format='json')
        self.assertEqual(reponse.status_code, 200)
        self.assertIn('refresh', reponse.data)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': ancien_refresh}, format='json').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': reponse.data['refresh']}, format='json').status_code, 200)

    def test_changer_mot_de_passe_ancien_incorrect(self):
        self.client.force_authenticate(self.user)
        reponse = self.client.post('/api/auth/changer-mot-de-passe/', {
            'ancien_password': 'faux', 'nouveau_password': 'Nouveau-Mdp-2026',
        }, format='json')
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(reponse.data['detail'], 'Le mot de passe actuel est incorrect.')

    def test_inscription_mot_de_passe_faible_refusee(self):
        reponse = self.client.post('/api/auth/register/', {
            'nom': 'Ba', 'prenom': 'Moussa', 'email': 'moussa@umred.sn', 'password': 'password',
        }, format='json')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('password', reponse.data)


class EmailInsensibleCasseTests(APITestCase):
    def setUp(self):
        cache.clear()
        Utilisateur.objects.create_user(email='Awa@UMRED.sn', password='Mdp-Solide-2026', nom='D', prenom='A', role=Role.CHERCHEUR)

    def test_email_stocke_en_minuscules_et_connexion_insensible_a_la_casse(self):
        self.assertTrue(Utilisateur.objects.filter(email='awa@umred.sn').exists())
        reponse = self.client.post('/api/auth/login/', {'email': 'AWA@umred.sn', 'password': 'Mdp-Solide-2026'}, format='json')
        self.assertEqual(reponse.status_code, 200)

    def test_pas_de_doublon_a_la_casse_pres(self):
        reponse = self.client.post('/api/auth/register/', {
            'nom': 'X', 'prenom': 'Y', 'email': 'AWA@umred.sn', 'password': 'Mdp-Solide-2026',
        }, format='json')
        self.assertEqual(reponse.status_code, 400)
