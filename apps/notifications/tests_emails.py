from django.core import mail
from django.test import TestCase

from apps.organisations.models import Organisation
from apps.utilisateurs.models import Role, Utilisateur

from .emails import COULEUR_SENLAB, envoyer_email
from .services import envoyer_email as envoyer_notification


class MiseEnPageEmailTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(nom='UCAD', slug='ucad', couleur_primaire='#0E7C66')
        self.user = Utilisateur.objects.create_user(email='awa@ucad.sn', password='x', nom='Diop', prenom='Awa',
                                                    role=Role.ETUDIANT, organisation=self.org)

    def html(self):
        return mail.outbox[0].alternatives[0][0]

    def test_version_html_et_texte(self):
        envoyer_email(self.user, 'Sujet', ['Premier paragraphe.'], bouton=('Ouvrir', 'http://exemple.sn/x'))
        message = mail.outbox[0]
        self.assertIn('http://exemple.sn/x', message.body)
        self.assertEqual(message.alternatives[0][1], 'text/html')
        self.assertIn('href="http://exemple.sn/x"', self.html())
        self.assertIn('Bonjour Awa', self.html())

    def test_couleur_et_nom_de_l_etablissement(self):
        envoyer_email(self.user, 'Sujet', ['Texte.'])
        self.assertIn('#0E7C66', self.html())
        self.assertIn('UCAD', self.html())

    def test_couleur_heritee_remplacee_par_la_charte(self):
        self.org.couleur_primaire = '#1848D9'
        self.org.save()
        envoyer_email(self.user, 'Sujet', ['Texte.'])
        self.assertIn(COULEUR_SENLAB, self.html())

    def test_contenu_echappe_dans_le_html(self):
        envoyer_notification(self.user, 'Alerte', 'Motif : <script>alert(1)</script>')
        self.assertNotIn('<script>', self.html())
        self.assertIn('&lt;script&gt;', self.html())
        # La version texte reste lisible, sans entités HTML.
        self.assertIn('<script>alert(1)</script>', mail.outbox[0].body)
