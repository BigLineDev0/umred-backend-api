from rest_framework.decorators import APIView, action
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.utilisateurs.models import Role, Utilisateur, JetonDefinitionMotDePasse
from rest_framework.exceptions import ValidationError as DRFValidationError
from .serializers import MonProfilUpdateSerializer, UmredTokenObtainPairSerializer, RegisterSerializer, UtilisateurCreateSerializer, UtilisateurSerializer, DefinirMotDePasseSerializer

from rest_framework import generics, mixins, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework import viewsets
from apps.core.services import enregistrer as journaliser
from .services import envoyer_lien_definition_mdp


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
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        utilisateur = Utilisateur.objects.create_user(
            email=serializer.validated_data['email'],
            password=None,  # aucun mot de passe utilisable tant qu'il n'a pas cliqué le lien
            nom=serializer.validated_data['nom'],
            prenom=serializer.validated_data['prenom'],
            telephone=serializer.validated_data.get('telephone', ''),
            role=serializer.validated_data['role'],
        )
        utilisateur.activer_compte()

        jeton = JetonDefinitionMotDePasse.generer_pour(utilisateur)
        try:
            envoyer_lien_definition_mdp(utilisateur, jeton.jeton)
        except Exception as e:
            print(f"ERREUR ENVOI EMAIL : {e}")

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
        journaliser(request.user, "Désactivation d'un compte", utilisateur)
        return Response(UtilisateurSerializer(utilisateur).data)
    
    
class UmredTokenObtainPairView(TokenObtainPairView):
    serializer_class = UmredTokenObtainPairSerializer
    

class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

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
        utilisateur.set_password(serializer.validated_data['password'])
        utilisateur.save()

        objet_jeton.utilise = True
        objet_jeton.save(update_fields=['utilise'])

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

    def post(self, request):
        ancien = request.data.get('ancien_password', '')
        nouveau = request.data.get('nouveau_password', '')

        if not request.user.check_password(ancien):
            return Response({'detail': 'Le mot de passe actuel est incorrect.'}, status=400)
        if len(nouveau) < 8:
            return Response({'detail': 'Le nouveau mot de passe doit contenir au moins 8 caractères.'}, status=400)

        request.user.set_password(nouveau)
        request.user.save()
        journaliser(request.user, 'Modification du mot de passe')
        return Response({'detail': 'Mot de passe modifié avec succès.'})