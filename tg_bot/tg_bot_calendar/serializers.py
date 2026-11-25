from rest_framework import serializers
from .models import Appointment, TelegramProfile


class AppoinmentSerializer(serializers.ModelSerializer):
    event_name = serializers.CharField(source="event.name", read_only=True)
    organizer_username = serializers.CharField(
        source="organizer.username", read_only=True, default=None
    )
    is_public = serializers.BooleanField(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "event_name",
            "date",
            "time",
            "details",
            "status",
            "is_public",
            "organizer_username",
            "telegram_id",
        ]


class TelegramProfileSerializer(serializers.ModelSerializer):

    class Meta:
        model = TelegramProfile
        fields = ["id", "telegram_id", "user"]
