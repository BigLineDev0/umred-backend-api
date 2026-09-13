from django.contrib import admin
from .models import Maintenance


@admin.register(Maintenance)
class MaintenanceAdmin(admin.ModelAdmin):
    list_display = ('equipement', 'type', 'technicien', 'date_planifiee', 'statut')
    list_filter = ('statut', 'type')
    search_fields = ('equipement__nom', 'description')
    autocomplete_fields = ('equipement', 'technicien')