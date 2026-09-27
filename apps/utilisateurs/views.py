import logging

from django.db import transaction
from rest_framework.decorators import APIView, action
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.utilisateurs.models import Role, Utilisateur, JetonDefinitionMotDePasse, MotifJeton, StatutCompte
from rest_framework.exceptions import ValidationError as DRFValidationError
from .serializers import (
    MonProfilUpdateSerializer, UmredTokenObtainPairSerializer, RegisterSerializer, UtilisateurCreateSerializer,
    UtilisateurSerializer, DefinirMotDePasseSerializer, ChangerMotDePasseSerializer, MotDePasseOublieSerializer,
    verifier_robustesse,
)

from rest_framework import generics, mixins, permissions, status
from rest_framework.response import Response
from rest_framework import viewsets
from apps.core.services import enregistrer as journaliser
from .services import envoyer_lien_definition_mdp, envoyer_lien_reinitialisation_mdp, revoquer_sessions

logger = logging.getLogger(__name__)


def _premier_message(erreurs):
    """
    Le frontend affiche err.error.detail : on aplatit les erreurs de
    validation DRF ({'champ': ['message', ...]}) en un seul message lisible.
    """
    if isinstance(erreurs, dict):
        erreurs = next(iter(erreurs.values()))
    if isinstance(erreurs, list):
        return _premier_message(erreurs[0]) if erreurs else ''
    return str(erreurs)


class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == Role.ADMIN


class UtilisateurViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          mixins.CreateModelMixin, mixins.UpdateModelMixin,
                          viewsets.GenericViewSet):
    """
    Pas de suppression exposée volontairement : un compte se désactive,
    il ne se supprime jamais — il reste lié à des réservations, des
    maintenances, un journal d'activité qui doivent conserver leur trace.
    """
    queryset = Utilisateur.objects.all()
    permission_classes = [EstAdmin]

    def get_serializer_class(self):
        return UtilisateurCreateSerializer if self.action == 'create' else UtilisateurSerializer

    def create(self, request, *args, **kwargs):
        """
        Flux d'invitation : l'admin crée le compte SANS mot de passe
        (set_password(None) produit un mot de passe inutilisable), un jeton
        aléatoire à usage unique est généré, puis envoyé par email. C'est
        l'utilisateur qui choisit lui-même son mot de passe via ce lien :
        l'admin ne connaît jamais le mot de passe de personne.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        utilisateur = Utilisateur.objects.create_user(
            email=serializer.validated_data['email'],
            password=None,  # aucun mot de passe utilisable tant qu'il n'a pas cliqué le lien
            nom=serializer.validated_data['nom'],
            prenom=serializer.validated_data['prenom'],
            telephone=serializer.validated_data.get('telephone', ''),
            role=serializer.validated_data['role'],
            statut_academique=serializer.validated_data.get('statut_academique'),
        )
        utilisateur.activer_compte()

        jeton = JetonDefinitionMotDePasse.generer_pour(utilisateur)
        try:
            envoyer_lien_definition_mdp(utilisateur, jeton.jeton)
        except Exception:
            # Le compte est créé même si l'email échoue ; l'erreur est
            # tracée dans les logs serveur (avec la pile d'appels).
            logger.exception("Échec d'envoi de l'email d'invitation à %s", utilisateur.email)

        journaliser(request.user, "Création d'un compte utilisateur", utilisateur, f'Rôle : {utilisateur.get_role_display()}')
        return Response(UtilisateurSerializer(utilisateur).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, "Modification d'un compte utilisateur", instance)

    @action(detail=True, methods=['post'])
    def activer(self, request, pk=None):
        utilisateur = self.get_object()
        utilisateur.activer_compte()
        journaliser(request.user, "Activation d'un compte", utilisateur)
        return Response(UtilisateurSerializer(utilisateur).data)

    @action(detail=True, methods=['post'])
    def desactiver(self, request, pk=None):
        utilisateur = self.get_object()
        if utilisateur.id == request.user.id:
            raise DRFValidationError("Vous ne pouvez pas désactiver votre propre compte.")
        utilisateur.desactiver_compte()
        revoquer_sessions(utilisateur)
        journaliser(request.user, "Désactivation d'un compte", utilisateur)
        return Response(UtilisateurSerializer(utilisateur).data)
    
    
class UmredTokenObtainPairView(TokenObtainPairView):
    serializer_class = UmredTokenObtainPairSerializer
    throttle_scope = 'auth'  # limite le brute-force (taux défini dans settings)
    

class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'auth'

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'role': user.role,
            'nom': user.nom,
            'prenom': user.prenom,
        }, status=status.HTTP_201_CREATED)

class LogoutView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        journaliser(request.user, 'Déconnexion de la plateforme', request.user)
        refresh = request.data.get('refresh')
        if refresh:
            try:
                RefreshToken(refresh).blacklist()
            except Exception:
                pass  # blacklist non configuré : la déconnexion reste journalisée quand même
        return Response(status=status.HTTP_205_RESET_CONTENT)
    
    

class VerifierJetonView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'auth'

    def get(self, request, jeton):
        try:
            objet_jeton = JetonDefinitionMotDePasse.objects.get(jeton=jeton)
        except JetonDefinitionMotDePasse.DoesNotExist:
            return Response({'valide': False}, status=404)

        if not objet_jeton.est_valide():
            return Response({'valide': False}, status=400)

        return Response({'valide': True, 'prenom': objet_jeton.utilisateur.prenom})


class DefinirMotDePasseView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'auth'

    def post(self, request):
        serializer = DefinirMotDePasseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            objet_jeton = JetonDefinitionMotDePasse.objects.get(jeton=serializer.validated_data['jeton'])
        except JetonDefinitionMotDePasse.DoesNotExist:
            return Response({'detail': 'Lien invalide.'}, status=404)

        if not objet_jeton.est_valide():
            return Response({'detail': 'Ce lien a expiré ou a déjà été utilisé.'}, status=400)

        utilisateur = objet_jeton.utilisateur
        try:
            verifier_robustesse(serializer.validated_data['password'], utilisateur)
        except DRFValidationError as e:
            return Response({'detail': _premier_message(e.detail)}, status=400)

        # set_password hache le mot de passe (PBKDF2 par défaut) : il n'est
        # jamais stocké en clair. Le jeton est marqué utilisé dans la même
        # transaction, pour qu'un même lien ne puisse pas servir deux fois.
        # Les sessions ouvertes sont révoquées : si le mot de passe a été
        # réinitialisé parce que le compte était compromis, l'intrus perd
        # immédiatement la possibilité de renouveler son token.
        with transaction.atomic():
            utilisateur.set_password(serializer.validated_data['password'])
            utilisateur.save(update_fields=['password'])
            objet_jeton.utilise = True
            objet_jeton.save(update_fields=['utilise'])
        revoquer_sessions(utilisateur)

        journaliser(utilisateur, 'Définition du mot de passe', utilisateur, objet_jeton.get_motif_display())
        return Response({'detail': 'Mot de passe défini avec succès.'})
    

class MonProfilView(generics.RetrieveUpdateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        return MonProfilUpdateSerializer if self.request.method in ('PUT', 'PATCH') else UtilisateurSerializer

    def perform_update(self, serializer):
        instance = serializer.save()
        journaliser(self.request.user, "Modification de son profil", instance)


class ChangerMotDePasseView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'auth'

    def post(self, request):
        serializer = ChangerMotDePasseSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({'detail': _premier_message(serializer.errors)}, status=400)

        request.user.set_password(serializer.validated_data['nouveau_password'])
        request.user.save(update_fields=['password'])

        # Toutes les sessions (y compris celle-ci) sont révoquées, puis une
        # nouvelle paire de tokens est renvoyée pour que l'utilisateur reste
        # connecté sur l'appareil qu'il utilise en ce moment.
        revoquer_sessions(request.user)
        refresh = RefreshToken.for_user(request.user)

        journaliser(request.user, 'Modification du mot de passe')
        return Response({
            'detail': 'Mot de passe modifié avec succès.',
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        })


class MotDePasseOublieView(APIView):
    """
    Demande de réinitialisation : envoie un lien (valable 1 h) vers la même
    page frontend que l'invitation, /definir-mot-de-passe/<jeton>.
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'auth'

    MESSAGE = ("Si un compte actif correspond à cette adresse, un email de "
               "réinitialisation vient d'être envoyé.")

    def post(self, request):
        serializer = MotDePasseOublieSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'detail': _premier_message(serializer.errors)}, status=400)

        utilisateur = Utilisateur.objects.filter(
            email__iexact=serializer.validated_data['email'],
            is_active=True, statut_compte=StatutCompte.ACTIF,
        ).first()

        if utilisateur:
            jeton = JetonDefinitionMotDePasse.generer_pour(utilisateur, MotifJeton.REINITIALISATION)
            try:
                envoyer_lien_reinitialisation_mdp(utilisateur, jeton.jeton)
            except Exception:
                logger.exception("Échec d'envoi de l'email de réinitialisation à %s", utilisateur.email)
            journaliser(utilisateur, 'Demande de réinitialisation du mot de passe', utilisateur)

        # Réponse IDENTIQUE que le compte existe ou non : sinon ce formulaire
        # permettrait à n'importe qui de tester quels emails sont inscrits
        # (énumération de comptes).
        return Response({'detail': self.MESSAGE})