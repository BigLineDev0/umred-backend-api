from django.conf import settings
from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.laboratoires.models import Laboratoire


class UniteConsommable(models.TextChoices):
    ML = 'ML', 'mL'
    L = 'L', 'L'
    G = 'G', 'g'
    KG = 'KG', 'kg'
    UNITE = 'UNITE', 'unité(s)'


class TypeMouvement(models.TextChoices):
    UTILISATION = 'UTILISATION', 'Utilisation'
    REAPPROVISIONNEMENT = 'REAPPROVISIONNEMENT', 'Réapprovisionnement'
    AJUSTEMENT = 'AJUSTEMENT', 'Ajustement (correction d’inventaire)'


class Consommable(models.Model):
    laboratoire = models.ForeignKey(Laboratoire, on_delete=models.CASCADE, related_name='consommables')
    nom = models.CharField(max_length=150)
    reference = models.CharField(max_length=100, blank=True)
    unite = models.CharField(max_length=10, choices=UniteConsommable.choices, default=UniteConsommable.UNITE)
    quantite_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    seuil_alerte = models.DecimalField(
        max_digits=10, decimal_places=2, default=1,
        help_text="Quantité en dessous de laquelle une alerte de stock faible se déclenche."
    )
    date_peremption = models.DateField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Consommable'
        verbose_name_plural = 'Consommables'
        ordering = ['nom']

    def __str__(self):
        return f'{self.nom} ({self.laboratoire})'

    @property
    def statut(self):
        """
        Calculé à la volée plutôt que stocké — évite toute désynchronisation
        entre la quantité réelle et un champ 'statut' qu'on oublierait de
        mettre à jour à chaque mouvement.
        """
        if self.date_peremption and self.date_peremption < timezone.now().date():
            return 'PERIME'
        if self.quantite_stock <= 0:
            return 'EPUISE'
        if self.quantite_stock <= self.seuil_alerte:
            return 'STOCK_FAIBLE'
        return 'DISPONIBLE'

    @property
    def peremption_proche(self):
        if not self.date_peremption:
            return False
        jours_restants = (self.date_peremption - timezone.now().date()).days
        return 0 <= jours_restants <= 15

    # Le stock n'est jamais modifié « en silence » : chaque retrait ou
    # réapprovisionnement crée une ligne MouvementStock (qui, quand,
    # combien, pourquoi). quantite_stock est la valeur courante,
    # MouvementStock en est l'historique traçable.

    def retirer_stock(self, quantite, utilisateur, motif=''):
        if quantite <= 0:
            raise ValidationError("La quantité doit être positive.")
        # Verrou sur la ligne + relecture de la quantité en base : deux
        # retraits simultanés s'exécutent l'un après l'autre, le second voit
        # le stock déjà diminué (pas de mise à jour perdue ni de stock
        # négatif). La transaction garantit aussi que stock et mouvement
        # sont enregistrés ensemble, ou pas du tout.
        with transaction.atomic():
            self.quantite_stock = Consommable.objects.select_for_update().get(pk=self.pk).quantite_stock
            if quantite > self.quantite_stock:
                raise ValidationError(f"Stock insuffisant : {self.quantite_stock} {self.get_unite_display()} disponible(s).")
            self.quantite_stock -= quantite
            self.save(update_fields=['quantite_stock'])
            MouvementStock.objects.create(
                consommable=self, type=TypeMouvement.UTILISATION,
                quantite=quantite, utilisateur=utilisateur, motif=motif,
            )

    def reapprovisionner(self, quantite, utilisateur, motif=''):
        if quantite <= 0:
            raise ValidationError("La quantité doit être positive.")
        with transaction.atomic():
            self.quantite_stock = Consommable.objects.select_for_update().get(pk=self.pk).quantite_stock
            self.quantite_stock += quantite
            self.save(update_fields=['quantite_stock'])
            MouvementStock.objects.create(
                consommable=self, type=TypeMouvement.REAPPROVISIONNEMENT,
                quantite=quantite, utilisateur=utilisateur, motif=motif,
            )

    def ajuster_stock(self, nouvelle_quantite, utilisateur, motif="Correction d'inventaire"):
        """
        Correction manuelle (inventaire physique, erreur de saisie) : la
        quantité est fixée directement, et l'écart est tracé comme un
        mouvement AJUSTEMENT (positif ou négatif).
        """
        if nouvelle_quantite < 0:
            raise ValidationError("Le stock ne peut pas être négatif.")
        with transaction.atomic():
            ancienne = Consommable.objects.select_for_update().get(pk=self.pk).quantite_stock
            ecart = nouvelle_quantite - ancienne
            self.quantite_stock = nouvelle_quantite
            if ecart == 0:
                return
            self.save(update_fields=['quantite_stock'])
            MouvementStock.objects.create(
                consommable=self, type=TypeMouvement.AJUSTEMENT,
                quantite=ecart, utilisateur=utilisateur, motif=motif,
            )


class MouvementStock(models.Model):
    consommable = models.ForeignKey(Consommable, on_delete=models.CASCADE, related_name='mouvements')
    type = models.CharField(max_length=25, choices=TypeMouvement.choices)
    quantite = models.DecimalField(max_digits=10, decimal_places=2)
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    motif = models.CharField(max_length=255, blank=True)
    date_mouvement = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Mouvement de stock'
        ordering = ['-date_mouvement']

    def __str__(self):
        return f'{self.get_type_display()} — {self.consommable.nom} ({self.quantite})'