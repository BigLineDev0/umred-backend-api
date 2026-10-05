import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.utilisateurs.models import Role, Utilisateur


class Command(BaseCommand):
    help = (
        "Crée le compte super-admin (éditeur de la plateforme SaaS). Il crée ensuite les "
        "établissements et leur administrateur depuis la console « Plateforme »."
    )

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True)
        parser.add_argument('--prenom', default='Super')
        parser.add_argument('--nom', default='Admin')

    def handle(self, *args, email, prenom, nom, **options):
        if Utilisateur.objects.filter(email__iexact=email).exists():
            raise CommandError(f'Un compte existe déjà avec {email}.')
        # Mot de passe saisi au clavier (jamais en argument : il resterait
        # dans l'historique du shell et la liste des processus).
        mot_de_passe = getpass.getpass('Mot de passe : ')
        if mot_de_passe != getpass.getpass('Confirmation : '):
            raise CommandError('Les mots de passe ne correspondent pas.')
        utilisateur = Utilisateur(email=email.lower(), prenom=prenom, nom=nom, role=Role.SUPER_ADMIN)
        try:
            validate_password(mot_de_passe, utilisateur)
        except ValidationError as e:
            raise CommandError(' '.join(e.messages))
        # Accès à l'admin Django (/admin/) en plus de la console plateforme.
        Utilisateur.objects.create_user(
            email=email, password=mot_de_passe, prenom=prenom, nom=nom,
            role=Role.SUPER_ADMIN, organisation=None, is_staff=True, is_superuser=True,
        )
        self.stdout.write(self.style.SUCCESS(f'Super-admin {email} créé. Connectez-vous puis créez un établissement.'))
