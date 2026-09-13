from django.contrib import admin
from .models import Equipement


@admin.register(Equipement)
class EquipementAdmin(admin.ModelAdmin):
    list_display = ('nom', 'laboratoire', 'numero_serie', 'statut')
    list_filter = ('statut', 'laboratoire')
    search_fields = ('nom', 'numero_serie', 'marque', 'modele')