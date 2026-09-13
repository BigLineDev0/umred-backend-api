from rest_framework import serializers
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    entite_type_nom = serializers.CharField(source='entite_type.model', read_only=True, allow_null=True)

    class Meta:
        model = Notification
        fields = ['id', 'titre', 'message', 'type', 'lu', 'entite_type_nom', 'entite_id', 'date_envoi']
        read_only_fields = fields