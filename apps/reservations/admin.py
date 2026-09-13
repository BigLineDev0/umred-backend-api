from django.contrib import admin
from .models import Reservation


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ('demandeur', 'laboratoire', 'equipement', 'date', 'heure_debut', 'heure_fin', 'statut')
    list_filter = ('statut', 'laboratoire')
    search_fields = ('demandeur__email', 'demandeur__nom', 'motif')
    autocomplete_fields = ('demandeur', 'validateur', 'laboratoire', 'equipement')