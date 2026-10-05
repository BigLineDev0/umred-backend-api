from django.core.cache import cache
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.utilisateurs.models import Role, Utilisateur

from .models import Organisation


class SuspensionEtablissementTests(APITestCase):
    """Une suspension coupe aussi les sessions déjà ouvertes, pas seulement les nouvelles connexions."""

    def setUp(self):
        cache.clear()
        self.organisation = Organisation.objects.create(nom='Université Test', slug='test')
        self.chercheur = Utilisateur.objects.create_user(
            email='cher@test.sn', password='x', nom='C', prenom='C', role=Role.CHERCHEUR,
            organisation=self.organisation,
        )
        self.editeur = Utilisateur.objects.create_user(
            email='editeur@test.sn', password='x', nom='E', prenom='E', role=Role.SUPER_ADMIN,
        )

    def _suspendre(self):
        self.client.force_authenticate(self.editeur)
        self.assertEqual(self.client.post(f'/api/organisations/{self.organisation.id}/suspendre/').status_code, 200)
        self.client.force_authenticate(None)

    def test_token_d_acces_refuse_apres_suspension(self):
        access = str(RefreshToken.for_user(self.chercheur).access_token)
        self._suspendre()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(self.client.get('/api/utilisateurs/moi/').status_code, 401)

    def test_refresh_refuse_apres_suspension(self):
        refresh = str(RefreshToken.for_user(self.chercheur))
        self._suspendre()
        reponse = self.client.post('/api/auth/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(reponse.status_code, 401)

    def test_refresh_accepte_pour_un_etablissement_actif(self):
        refresh = str(RefreshToken.for_user(self.chercheur))
        reponse = self.client.post('/api/auth/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(reponse.status_code, 200)
        self.assertIn('access', reponse.data)

    def test_reactivation_rend_l_acces_avec_un_nouveau_token(self):
        self._suspendre()
        self.organisation.est_active = True
        self.organisation.save(update_fields=['est_active'])
        access = str(RefreshToken.for_user(self.chercheur).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(self.client.get('/api/utilisateurs/moi/').status_code, 200)
