from django.contrib import admin
from .models import Organisation


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ('nom', 'slug', 'ville', 'est_active', 'date_creation')
    list_filter = ('est_active',)
    search_fields = ('nom', 'slug', 'ville')
    prepopulated_fields = {'slug': ('nom',)}
