from django.conf import settings
from django.db import models
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

    def retirer_stock(self, quantite, utilisateur, motif=''):
        if quantite <= 0:
            raise ValidationError("La quantité doit être positive.")
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
        self.quantite_stock += quantite
        self.save(update_fields=['quantite_stock'])
        MouvementStock.objects.create(
            consommable=self, type=TypeMouvement.REAPPROVISIONNEMENT,
            quantite=quantite, utilisateur=utilisateur, motif=motif,
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