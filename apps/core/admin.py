from django.contrib import admin
from .models import JournalActivite


@admin.register(JournalActivite)
class JournalActiviteAdmin(admin.ModelAdmin):
    list_display = ('date_heure', 'auteur', 'action', 'entite_type', 'entite_id')
    list_filter = ('action', 'entite_type')
    search_fields = ('description',)
    readonly_fields = [f.name for f in JournalActivite._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False