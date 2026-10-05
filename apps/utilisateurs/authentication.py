"""
Contrôles d'accès appliqués à CHAQUE requête authentifiée et à chaque
renouvellement de token, et pas seulement à la connexion.

Sans eux, un établissement suspendu par l'éditeur gardait l'accès tant que
ses sessions restaient ouvertes : la rotation des refresh tokens les
prolongeait indéfiniment, et simplejwt (5.3) ne vérifie pas l'utilisateur
au moment du rafraîchissement.
"""
from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.organisations.isolation import est_super_admin

from .models import StatutCompte, Utilisateur


def verifier_acces(user):
    """Lève AuthenticationFailed si le compte ou son établissement n'a plus accès."""
    if not user.is_active or user.statut_compte != StatutCompte.ACTIF:
        raise AuthenticationFailed("Ce compte n'est pas actif.", code='compte_inactif')
    organisation = user.organisation
    if not est_super_admin(user) and organisation is not None and not organisation.est_active:
        raise AuthenticationFailed(
            "L'accès de votre établissement à la plateforme est suspendu.", code='organisation_suspendue',
        )


class JWTAuthenticationEtablissement(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        verifier_acces(user)
        return user


class TokenRefreshEtablissementSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        user_id = RefreshToken(attrs['refresh']).payload.get(api_settings.USER_ID_CLAIM)
        user = Utilisateur.objects.select_related('organisation').filter(pk=user_id).first()
        if user is None:
            raise AuthenticationFailed("Utilisateur introuvable.", code='user_not_found')
        verifier_acces(user)
        return super().validate(attrs)


class JWTEtablissementScheme(SimpleJWTScheme):
    """Documente l'authentification dans Swagger comme le JWT standard."""
    target_class = 'apps.utilisateurs.authentication.JWTAuthenticationEtablissement'
