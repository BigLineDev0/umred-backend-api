from unittest.mock import patch

from django.core.cache import cache
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.organisations.models import Organisation
from .models import Utilisateur, Role, StatutAcademique


def organisation_test():
    return Organisation.objects.get_or_create(slug='test', defaults={'nom': 'Université Test'})[0]


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
            'organisation': organisation_test().id,
        }, format='json')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('password', reponse.data)


class ActivationParEmailTests(APITestCase):
    MDP = 'Mdp-Solide-2026'

    def setUp(self):
        cache.clear()

    def _inscrire(self, email='moussa@umred.sn'):
        return self.client.post('/api/auth/register/', {
            'nom': 'Ba', 'prenom': 'Moussa', 'email': email, 'password': self.MDP,
            'organisation': organisation_test().id,
        }, format='json')

    def _jeton(self):
        from .models import JetonDefinitionMotDePasse, MotifJeton
        return JetonDefinitionMotDePasse.objects.get(motif=MotifJeton.VERIFICATION, utilise=False)

    def _connexion(self, password=None):
        return self.client.post('/api/auth/login/', {
            'email': 'moussa@umred.sn', 'password': password or self.MDP,
        }, format='json')

    def test_inscription_cree_un_compte_bloque_et_envoie_le_lien(self):
        from django.core import mail
        from .models import StatutCompte

        reponse = self._inscrire()

        self.assertEqual(reponse.status_code, 201)
        self.assertNotIn('access', reponse.data)
        user = Utilisateur.objects.get(email='moussa@umred.sn')
        self.assertEqual(user.statut_compte, StatutCompte.EN_ATTENTE)
        self.assertFalse(user.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f'/activer-compte/{self._jeton().jeton}', mail.outbox[0].body)

    def test_connexion_refusee_avant_activation(self):
        self._inscrire()
        reponse = self._connexion()
        self.assertEqual(reponse.status_code, 401)
        self.assertEqual(reponse.data['code'], 'email_non_verifie')

    def test_mauvais_mot_de_passe_ne_revele_pas_le_compte_en_attente(self):
        self._inscrire()
        reponse = self._connexion(password='Faux-Mdp-2026')
        self.assertEqual(reponse.status_code, 401)
        self.assertNotEqual(reponse.data.get('code'), 'email_non_verifie')

    def test_activation_puis_connexion(self):
        from .models import StatutCompte
        self._inscrire()
        jeton = self._jeton()

        reponse = self.client.post('/api/auth/activer-compte/', {'jeton': jeton.jeton}, format='json')

        self.assertEqual(reponse.status_code, 200)
        user = Utilisateur.objects.get(email='moussa@umred.sn')
        self.assertEqual(user.statut_compte, StatutCompte.ACTIF)
        self.assertTrue(user.is_active)
        self.assertEqual(self._connexion().status_code, 200)
        # Second clic sur le même lien : pas une erreur.
        second = self.client.post('/api/auth/activer-compte/', {'jeton': jeton.jeton}, format='json')
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data['deja_active'])

    def test_lien_d_activation_expire(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import JetonDefinitionMotDePasse
        self._inscrire()
        jeton = self._jeton()
        JetonDefinitionMotDePasse.objects.filter(pk=jeton.pk).update(date_creation=timezone.now() - timedelta(hours=25))

        reponse = self.client.post('/api/auth/activer-compte/', {'jeton': jeton.jeton}, format='json')

        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(Utilisateur.objects.get(email='moussa@umred.sn').is_active)

    def test_compte_desactive_ne_se_reactive_pas_avec_le_lien(self):
        self._inscrire()
        jeton = self._jeton()
        Utilisateur.objects.get(email='moussa@umred.sn').desactiver_compte()

        reponse = self.client.post('/api/auth/activer-compte/', {'jeton': jeton.jeton}, format='json')

        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(Utilisateur.objects.get(email='moussa@umred.sn').is_active)

    def test_jeton_d_activation_refuse_pour_definir_le_mot_de_passe(self):
        self._inscrire()
        jeton = self._jeton()
        self.assertEqual(self.client.get(f'/api/auth/verifier-jeton/{jeton.jeton}/').status_code, 404)
        reponse = self.client.post('/api/auth/definir-mot-de-passe/',
                                   {'jeton': jeton.jeton, 'password': 'Autre-Mdp-2026'}, format='json')
        self.assertEqual(reponse.status_code, 404)

    def test_renvoi_du_lien_invalide_l_ancien(self):
        from django.core import mail
        self._inscrire()
        ancien = self._jeton()

        reponse = self.client.post('/api/auth/renvoyer-activation/', {'email': 'MOUSSA@umred.sn'}, format='json')

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(len(mail.outbox), 2)
        ancien.refresh_from_db()
        self.assertFalse(ancien.est_valide())
        self.assertIn(self._jeton().jeton, mail.outbox[1].body)

    def test_renvoi_meme_reponse_si_email_inconnu_ou_deja_actif(self):
        from django.core import mail
        Utilisateur.objects.create_user(email='actif@umred.sn', password=self.MDP, nom='A', prenom='A', role=Role.ETUDIANT)
        self._inscrire()
        en_attente = self.client.post('/api/auth/renvoyer-activation/', {'email': 'moussa@umred.sn'}, format='json')
        actif = self.client.post('/api/auth/renvoyer-activation/', {'email': 'actif@umred.sn'}, format='json')
        inconnu = self.client.post('/api/auth/renvoyer-activation/', {'email': 'personne@umred.sn'}, format='json')
        self.assertEqual(en_attente.data, actif.data)
        self.assertEqual(en_attente.data, inconnu.data)
        # 1 email d'inscription + 1 renvoi (le compte en attente uniquement).
        self.assertEqual(len(mail.outbox), 2)


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
            'organisation': organisation_test().id,
        }, format='json')
        self.assertEqual(reponse.status_code, 400)
        self.assertIn('email', reponse.data)
