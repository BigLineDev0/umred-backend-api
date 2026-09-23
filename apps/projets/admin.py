from django.contrib import admin

from apps.projets.models import Projet

@admin.register(Projet)
class ProjetAdmin(admin.ModelAdmin):
    list_display = ('nom', 'niveau_priorite', 'responsable')
    list_filter = ('niveau_priorite', 'responsable')
    search_fields = ('nom', 'description')