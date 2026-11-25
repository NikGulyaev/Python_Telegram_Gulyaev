from rest_framework import viewsets, permissions
from .models import Appointment, TelegramProfile
from .serializers import AppoinmentSerializer, TelegramProfileSerializer


class IsOwnerOrAdmin(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_staff


class AppointmentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AppoinmentSerializer
    permission_classes = [IsOwnerOrAdmin]

    def get_queryset(self):
        qs = Appointment.objects.select_related("event", "organizer")

        telegram_id = self.request.query_params.get("telegram_id")
        if telegram_id:
            qs = qs.filter(telegram_id=telegram_id)
            qs = qs.filter(is_public=True)
        else:
            qs = qs.none()
        return qs.order_by("date", "time")


class TelegramProfileViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TelegramProfile.objects.all()
    serializer_class = TelegramProfileSerializer
