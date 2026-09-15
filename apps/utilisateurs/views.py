from rest_framework.decorators import action
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.utilisateurs.models import Role, Utilisateur
from rest_framework.exceptions import ValidationError as DRFValidationError
from .serializers import UmredTokenObtainPairSerializer, RegisterSerializer, UtilisateurCreateSerializer, UtilisateurSerializer

from rest_framework import generics, mixins, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework import viewsets
from apps.core.services import enregistrer as journaliser
from .services import generer_mot_de_passe, envoyer_identifiants


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

        mot_de_passe = generer_mot_de_passe()
        utilisateur = Utilisateur.objects.create_user(
            email=serializer.validated_data['email'],
            password=mot_de_passe,
            nom=serializer.validated_data['nom'],
            prenom=serializer.validated_data['prenom'],
            telephone=serializer.validated_data.get('telephone', ''),
            role=serializer.validated_data['role'],
        )
        # Un compte créé par un admin est déjà de confiance : actif
        # immédiatement, contrairement à l'auto-inscription étudiante.
        utilisateur.activer_compte()

        try:
            envoyer_identifiants(utilisateur, mot_de_passe)
        except Exception:
            # La création du compte ne doit jamais échouer à cause d'un
            # envoi d'email en panne (SMTP non configuré, etc.) — le compte
            # existe déjà en base, l'admin pourra transmettre l'accès autrement.
            pass

        journaliser(request.user, "Création d'un compte utilisateur", utilisateur,
                    f'Rôle : {utilisateur.get_role_display()}')

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