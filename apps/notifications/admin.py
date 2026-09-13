from django.contrib import admin
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('titre', 'destinataire', 'type', 'lu', 'date_envoi')
    list_filter = ('type', 'lu')
    search_fields = ('titre', 'message')