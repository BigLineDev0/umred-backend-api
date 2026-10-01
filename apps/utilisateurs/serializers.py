from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from django.contrib.auth.models import update_last_login
from apps.core.services import enregistrer as journaliser
from .models import StatutCompte

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions, serializers
from apps.organisations.isolation import ChampsOrganisationMixin, est_super_admin
from apps.organisations.models import Organisation
from .models import Utilisateur, Role


def verifier_robustesse(password, utilisateur):
    """
    Applique les AUTH_PASSWORD_VALIDATORS de settings.py : longueur
    minimale, mot de passe trop courant, entièrement numérique, ou trop
    proche du nom/prénom/email de l'utilisateur (d'où le paramètre
    utilisateur). Convertit l'erreur Django en erreur DRF (réponse 400).
    """
    try:
        validate_password(password, utilisateur)
    except DjangoValidationError as e:
        raise serializers.ValidationError({'password': e.messages})


def verifier_email_unique(email, instance=None):
    # Comparaison insensible à la casse, et stockage en minuscules.
    email = email.lower()
    qs = Utilisateur.objects.filter(email__iexact=email)
    if instance is not None:
        qs = qs.exclude(pk=instance.pk)
    if qs.exists():
        raise serializers.ValidationError('Un compte existe déjà avec cette adresse email.')
    return email


class UmredTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role  # accessible par FastAPI sans rappeler Django
        token['prenom'] = user.prenom
        token['nom'] = user.nom
        token['organisation'] = user.organisation_id
        return token
    
    def validate(self, attrs):
        # super().validate() vérifie email + mot de passe et génère les
        # tokens ; un compte désactivé (is_active=False) y est déjà refusé.
        # On ajoute ensuite une règle métier : un compte non ACTIF (ex. EN_ATTENTE)
        # ne peut pas se connecter, même avec le bon mot de passe.
        # simplejwt lève l'AuthenticationFailed de DRF (classe parente de
        # la sienne) : c'est donc celle-là qu'il faut intercepter.
        try:
            data = super().validate(attrs)
        except exceptions.AuthenticationFailed:
            self._refuser_si_email_non_verifie(attrs)
            raise

        if self.user.statut_compte != StatutCompte.ACTIF:
            raise AuthenticationFailed(
                "Ce compte n'est pas encore actif. Contactez un administrateur.",
                code='compte_inactif'
            )
        # Abonnement suspendu par l'éditeur : tout l'établissement est bloqué.
        organisation = self.user.organisation
        if not est_super_admin(self.user) and organisation is not None and not organisation.est_active:
            raise AuthenticationFailed(
                "L'accès de votre établissement à la plateforme est suspendu.",
                code='organisation_suspendue'
            )

        update_last_login(None, self.user)
        journaliser(self.user, 'Connexion à la plateforme', self.user)

        data['role'] = self.user.role
        data['nom'] = self.user.nom
        data['prenom'] = self.user.prenom
        data['photo'] = self.user.photo.url if self.user.photo else None
        data['organisation'] = self.user.organisation_id
        return data

    def _refuser_si_email_non_verifie(self, attrs):
        # Un compte inscrit mais pas encore confirmé a is_active=False :
        # simplejwt le refuse avec le message générique « identifiants
        # invalides ». On donne un message précis (et un code que le
        # frontend reconnaît pour proposer de renvoyer l'email), mais
        # seulement si le mot de passe est correct : sinon ce message
        # révélerait qu'un compte existe pour cette adresse.
        utilisateur = Utilisateur.objects.filter(
            email__iexact=attrs.get(self.username_field, ''), statut_compte=StatutCompte.EN_ATTENTE,
        ).first()
        if utilisateur and utilisateur.check_password(attrs.get('password', '')):
            raise AuthenticationFailed(
                "Votre adresse email n'a pas encore été confirmée. "
                "Cliquez sur le lien d'activation reçu par email.",
                code='email_non_verifie'
            )


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    # L'étudiant choisit son établissement parmi ceux qui sont actifs.
    organisation = serializers.PrimaryKeyRelatedField(queryset=Organisation.objects.filter(est_active=True))

    class Meta:
        model = Utilisateur
        fields = ['nom', 'prenom', 'email', 'password', 'organisation']

    def validate_email(self, value):
        return verifier_email_unique(value)

    def validate(self, attrs):
        # Utilisateur temporaire (non sauvegardé) pour que le validateur de
        # similarité puisse comparer le mot de passe au nom/prénom/email.
        verifier_robustesse(attrs['password'], Utilisateur(
            email=attrs['email'], nom=attrs['nom'], prenom=attrs['prenom'],
        ))
        return attrs

    def create(self, validated_data):
        return Utilisateur.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            nom=validated_data['nom'],
            prenom=validated_data['prenom'],
            organisation=validated_data['organisation'],
            role=Role.ETUDIANT,   # jamais fourni par le client, toujours forcé ici
            # Compte bloqué jusqu'à la confirmation de l'adresse email
            # (voir ActiverCompteView) : is_active=False empêche toute
            # connexion, statut EN_ATTENTE l'affiche côté admin.
            statut_compte=StatutCompte.EN_ATTENTE,
            is_active=False,
        )
        
def verifier_encadrant(attrs, instance=None):
    """Seul un étudiant a un encadrant, et c'est un enseignant-chercheur."""
    role = attrs.get('role', getattr(instance, 'role', None))
    encadrant = attrs.get('encadrant', getattr(instance, 'encadrant', None))
    if encadrant is None:
        return attrs
    if role != Role.ETUDIANT:
        raise serializers.ValidationError({'encadrant': "Seul un étudiant peut être rattaché à un encadrant."})
    if encadrant.role != Role.CHERCHEUR:
        raise serializers.ValidationError({'encadrant': "L'encadrant doit être un enseignant-chercheur."})
    return attrs


# Un admin ne crée jamais de super-admin : ce rôle est réservé à l'éditeur.
ROLES_ETABLISSEMENT = [(r.value, r.label) for r in Role if r != Role.SUPER_ADMIN]


class UtilisateurSerializer(ChampsOrganisationMixin, serializers.ModelSerializer):
    champs_organisation = {'encadrant': 'organisation'}
    encadrant_nom = serializers.CharField(source='encadrant.nom_complet', read_only=True, allow_null=True)
    organisation_nom = serializers.CharField(source='organisation.nom', read_only=True, allow_null=True)
    role = serializers.ChoiceField(choices=ROLES_ETABLISSEMENT)

    class Meta:
        model = Utilisateur
        fields = [
            'id', 'nom', 'prenom', 'email', 'telephone', 'role', 'photo', 'statut_compte', 'statut_academique',
            'encadrant', 'encadrant_nom', 'organisation', 'organisation_nom', 'date_creation', 'last_login',
        ]
        read_only_fields = ['statut_compte', 'organisation', 'date_creation', 'last_login']

    def validate_email(self, value):
        return verifier_email_unique(value, self.instance)

    def validate(self, attrs):
        return verifier_encadrant(attrs, self.instance)


class UtilisateurCreateSerializer(ChampsOrganisationMixin, serializers.ModelSerializer):
    champs_organisation = {'encadrant': 'organisation'}
    role = serializers.ChoiceField(choices=ROLES_ETABLISSEMENT)

    class Meta:
        model = Utilisateur
        fields = ['nom', 'prenom', 'email', 'telephone', 'role', 'statut_academique', 'encadrant']

    def validate_email(self, value):
        return verifier_email_unique(value)

    def validate(self, attrs):
        return verifier_encadrant(attrs)


class DefinirMotDePasseSerializer(serializers.Serializer):
    jeton = serializers.CharField()
    password = serializers.CharField(min_length=8, write_only=True)


class ChangerMotDePasseSerializer(serializers.Serializer):
    ancien_password = serializers.CharField(write_only=True)
    nouveau_password = serializers.CharField(write_only=True)

    def validate_ancien_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Le mot de passe actuel est incorrect.')
        return value

    def validate(self, attrs):
        try:
            validate_password(attrs['nouveau_password'], self.context['request'].user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({'nouveau_password': e.messages})
        return attrs


class MotDePasseOublieSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ActiverCompteSerializer(serializers.Serializer):
    jeton = serializers.CharField()
    
    
class MonProfilUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['nom', 'prenom', 'telephone', 'photo']

    def validate_photo(self, value):
        if value and value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("L'image ne doit pas dépasser 5 Mo.")
        return value