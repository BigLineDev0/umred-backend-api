import logging

from django.db import transaction
from rest_framework.decorators import APIView, action
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.utilisateurs.models import Role, Utilisateur, JetonDefinitionMotDePasse, MotifJeton, StatutCompte
from rest_framework.exceptions import ValidationError as DRFValidationError
from .serializers import (
    AssignationEncadrantSerializer, MonProfilUpdateSerializer, UmredTokenObtainPairSerializer, RegisterSerializer, UtilisateurCreateSerializer,
    UtilisateurSerializer, DefinirMotDePasseSerializer, ChangerMotDePasseSerializer, MotDePasseOublieSerializer,
    ActiverCompteSerializer, verifier_robustesse,
)

from rest_framework import generics, mixins, permissions, status
from rest_framework.response import Response
from rest_framework import viewsets
from apps.core.services import enregistrer as journaliser
from apps.notifications.models import TypeNotification
from apps.notifications.services import notifier
from apps.organisations.isolation import filtrer_par_organisation
from .services import (
    envoyer_lien_definition_mdp, envoyer_lien_reinitialisation_mdp, envoyer_lien_activation, revoquer_sessions,
)

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers

logger = logging.getLogger(__name__)

# Réponse type {"detail": "..."} des vues d'authentification, pour Swagger.
REPONSE_DETAIL = inline_serializer('ReponseDetail', {'detail': drf_serializers.CharField()})

# Jetons qui permettent de choisir un mot de passe. Un jeton de
# vérification d'email ne sert qu'à activer le compte : il est refusé par
# les vues de définition du mot de passe.
MOTIFS_MOT_DE_PASSE = [MotifJeton.INVITATION, MotifJeton.REINITIALISATION]


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

    def get_permissions(self):
        if self.action in ['mes_etudiants', 'encadrants']:
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = filtrer_par_organisation(Utilisateur.objects.all(), self.request.user).select_related('encadrant', 'organisation')
        role = self.request.query_params.get('role')
        if role:
            qs = qs.filter(role=role)
        return qs

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
            encadrant=serializer.validated_data.get('encadrant'),
            # Toujours l'établissement de l'admin, jamais lu dans la requête.
            organisation=request.user.organisation,
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

    @action(detail=False, methods=['get'])
    def mes_etudiants(self, request):
        """Les étudiants encadrés par l'enseignant-chercheur connecté."""
        etudiants = request.user.etudiants_encadres.filter(statut_compte=StatutCompte.ACTIF)
        return Response(UtilisateurSerializer(etudiants, many=True).data)

    @action(detail=False, methods=['get'])
    def encadrants(self, request):
        """Enseignants-chercheurs actifs de l'établissement (liste légère pour les formulaires)."""
        encadrants = filtrer_par_organisation(Utilisateur.objects.all(), request.user).filter(
            role=Role.CHERCHEUR, statut_compte=StatutCompte.ACTIF,
        )
        return Response([{'id': u.id, 'nom_complet': u.nom_complet} for u in encadrants])

    @action(detail=False, methods=['post'])
    def assigner_encadrant(self, request):
        """
        Assignation groupée d'un encadrant (ou retrait, encadrant = null).
        Tout ou rien : un identifiant inconnu, hors établissement ou qui
        n'est pas un étudiant refuse l'ensemble de la demande. L'encadrant
        est prévenu par une notification.
        """
        comptes = filtrer_par_organisation(Utilisateur.objects.all(), request.user)
        serializer = AssignationEncadrantSerializer(
            data=request.data,
            encadrants=comptes.filter(role=Role.CHERCHEUR, statut_compte=StatutCompte.ACTIF),
            etudiants=comptes.filter(role=Role.ETUDIANT),
        )
        serializer.is_valid(raise_exception=True)
        encadrant = serializer.validated_data['encadrant']
        etudiants = serializer.validated_data['etudiants']
        a_modifier = [e for e in etudiants if e.encadrant_id != (encadrant.id if encadrant else None)]

        with transaction.atomic():
            Utilisateur.objects.filter(pk__in=[e.pk for e in a_modifier]).update(encadrant=encadrant)
            for etudiant in a_modifier:
                journaliser(request.user, "Assignation d'un encadrant", etudiant,
                            f'Encadrant : {encadrant.nom_complet}' if encadrant else 'Encadrant retiré')

        if encadrant and a_modifier:
            noms = ', '.join(e.nom_complet for e in a_modifier[:5]) + (' …' if len(a_modifier) > 5 else '')
            nombre = len(a_modifier)
            notifier(encadrant, 'Nouveaux étudiants encadrés',
                     f"{nombre} étudiant{'s' if nombre > 1 else ''} vous {'ont' if nombre > 1 else 'a'} été "
                     f"rattaché{'s' if nombre > 1 else ''} : {noms}. Vous recevrez leurs demandes de réservation.",
                     TypeNotification.SYSTEME)

        mis_a_jour = Utilisateur.objects.filter(pk__in=[e.pk for e in etudiants]).select_related('encadrant', 'organisation')
        return Response({
            'modifies': len(a_modifier),
            'inchanges': len(etudiants) - len(a_modifier),
            'etudiants': UtilisateurSerializer(mis_a_jour, many=True).data,
        })

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
        from apps.reservations.services import annuler_reservations_futures

        utilisateur.desactiver_compte()
        revoquer_sessions(utilisateur)
        annulees = annuler_reservations_futures(utilisateur, par=request.user)
        journaliser(request.user, "Désactivation d'un compte", utilisateur,
                    f'{annulees} réservation(s) à venir annulée(s)' if annulees else '')
        return Response(UtilisateurSerializer(utilisateur).data)
    
    
class UmredTokenObtainPairView(TokenObtainPairView):
    serializer_class = UmredTokenObtainPairSerializer
    throttle_scope = 'auth'  # limite le brute-force (taux défini dans settings)
    

class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'auth'

    def create(self, request, *args, **kwargs):
        """
        Le compte est créé bloqué (EN_ATTENTE, is_active=False) et aucun
        token n'est renvoyé : l'utilisateur doit d'abord cliquer le lien
        d'activation reçu par email. On contrôle ainsi que l'adresse existe
        et appartient bien à la personne qui s'inscrit.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Compte et jeton créés ensemble : pas de compte sans lien d'activation.
        with transaction.atomic():
            user = serializer.save()
            jeton = JetonDefinitionMotDePasse.generer_pour(user, MotifJeton.VERIFICATION)
        try:
            envoyer_lien_activation(user, jeton.jeton)
        except Exception:
            # Le compte reste créé : l'utilisateur pourra redemander le lien
            # (RenvoyerActivationView).
            logger.exception("Échec d'envoi de l'email d'activation à %s", user.email)

        journaliser(user, 'Inscription sur la plateforme', user)
        return Response({
            'detail': f"Un email d'activation a été envoyé à {user.email}.",
            'email': user.email,
        }, status=status.HTTP_201_CREATED)

class LogoutView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=inline_serializer('Deconnexion', {'refresh': drf_serializers.CharField(required=False)}),
        responses={205: None}, summary='Se déconnecter (le refresh token est mis sur liste noire)',
    )
    def post(self, request):
        journaliser(request.user, 'Déconnexion de la plateforme', request.user)
        refresh = request.data.get('refresh')
        if refresh:
            try:
                RefreshToken(refresh).blacklist()
            except TokenError:
                # Token déjà expiré ou révoqué : la session est de toute façon close.
                pass
        return Response(status=status.HTTP_205_RESET_CONTENT)
    
    

class VerifierJetonView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'auth'

    @extend_schema(
        responses=inline_serializer('VerificationJeton', {
            'valide': drf_serializers.BooleanField(), 'prenom': drf_serializers.CharField(required=False),
        }),
        summary="Vérifier un lien d'invitation ou de réinitialisation",
    )
    def get(self, request, jeton):
        try:
            objet_jeton = JetonDefinitionMotDePasse.objects.get(jeton=jeton, motif__in=MOTIFS_MOT_DE_PASSE)
        except JetonDefinitionMotDePasse.DoesNotExist:
            return Response({'valide': False}, status=404)

        if not objet_jeton.est_valide():
            return Response({'valide': False}, status=400)

        return Response({'valide': True, 'prenom': objet_jeton.utilisateur.prenom})


class DefinirMotDePasseView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'auth'

    @extend_schema(request=DefinirMotDePasseSerializer, responses=REPONSE_DETAIL,
                   summary='Choisir son mot de passe via un lien reçu par email')
    def post(self, request):
        serializer = DefinirMotDePasseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            objet_jeton = JetonDefinitionMotDePasse.objects.get(
                jeton=serializer.validated_data['jeton'], motif__in=MOTIFS_MOT_DE_PASSE
            )
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

    @extend_schema(
        request=ChangerMotDePasseSerializer,
        responses=inline_serializer('ChangementMotDePasse', {
            'detail': drf_serializers.CharField(), 'access': drf_serializers.CharField(),
            'refresh': drf_serializers.CharField(),
        }),
        summary='Changer son mot de passe (renvoie une nouvelle paire de tokens)',
    )
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

    @extend_schema(request=MotDePasseOublieSerializer, responses=REPONSE_DETAIL,
                   summary='Demander un lien de réinitialisation du mot de passe')
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


class ActiverCompteView(APIView):
    """
    Confirmation de l'adresse email après une inscription libre : le lien
    reçu par email pointe vers la page frontend /activer-compte/<jeton>,
    qui appelle cet endpoint.
    """
    permission_classes = [permissions.AllowAny]
    # Aucune authentification : un vieux token resté dans le navigateur ne
    # doit pas faire échouer l'activation en 401.
    authentication_classes = []
    throttle_scope = 'auth'

    @extend_schema(
        request=ActiverCompteSerializer,
        responses=inline_serializer('ActivationCompte', {
            'detail': drf_serializers.CharField(), 'deja_active': drf_serializers.BooleanField(),
        }),
        summary="Activer son compte via le lien reçu après l'inscription",
    )
    def post(self, request):
        serializer = ActiverCompteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        objet_jeton = JetonDefinitionMotDePasse.objects.select_related('utilisateur').filter(
            jeton=serializer.validated_data['jeton'], motif=MotifJeton.VERIFICATION,
        ).first()
        if not objet_jeton:
            return Response({'detail': "Lien d'activation invalide."}, status=404)

        utilisateur = objet_jeton.utilisateur
        # Double clic sur le lien, ou lien ouvert au préalable par l'antivirus
        # de la messagerie : le compte est déjà actif, ce n'est pas une erreur.
        if utilisateur.statut_compte == StatutCompte.ACTIF:
            return Response({'detail': 'Votre compte est déjà activé.', 'deja_active': True})
        # Un compte désactivé par l'admin entre-temps ne se réactive pas
        # avec son ancien lien d'inscription.
        if utilisateur.statut_compte != StatutCompte.EN_ATTENTE:
            return Response({'detail': 'Ce compte a été désactivé. Contactez un administrateur.'}, status=400)
        if not objet_jeton.est_valide():
            return Response({'detail': "Ce lien d'activation a expiré."}, status=400)

        # Le jeton est marqué utilisé dans la même transaction que
        # l'activation : un même lien ne peut servir qu'une fois.
        with transaction.atomic():
            utilisateur.activer_compte()
            objet_jeton.utilise = True
            objet_jeton.save(update_fields=['utilise'])

        journaliser(utilisateur, "Activation du compte par email", utilisateur)
        return Response({'detail': 'Votre compte est activé. Vous pouvez vous connecter.', 'deja_active': False})


class RenvoyerActivationView(APIView):
    """
    Renvoie un lien d'activation (lien expiré, email perdu ou non reçu).
    Générer un nouveau jeton invalide le précédent.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_scope = 'auth'

    MESSAGE = ("Si un compte en attente d'activation correspond à cette adresse, "
               "un nouvel email d'activation vient d'être envoyé.")

    @extend_schema(request=MotDePasseOublieSerializer, responses=REPONSE_DETAIL,
                   summary="Renvoyer l'email d'activation")
    def post(self, request):
        serializer = MotDePasseOublieSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'detail': _premier_message(serializer.errors)}, status=400)

        utilisateur = Utilisateur.objects.filter(
            email__iexact=serializer.validated_data['email'], statut_compte=StatutCompte.EN_ATTENTE,
        ).first()

        if utilisateur:
            jeton = JetonDefinitionMotDePasse.generer_pour(utilisateur, MotifJeton.VERIFICATION)
            try:
                envoyer_lien_activation(utilisateur, jeton.jeton)
            except Exception:
                logger.exception("Échec d'envoi de l'email d'activation à %s", utilisateur.email)

        # Même réponse dans tous les cas (pas d'énumération de comptes).
        return Response({'detail': self.MESSAGE})