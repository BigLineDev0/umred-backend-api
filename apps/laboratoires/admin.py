from django.contrib import admin
from .models import Laboratoire


@admin.register(Laboratoire)
class LaboratoireAdmin(admin.ModelAdmin):
    list_display = ('nom', 'localisation', 'statut', 'responsable')
    list_filter = ('statut',)
    search_fields = ('nom', 'localisation')