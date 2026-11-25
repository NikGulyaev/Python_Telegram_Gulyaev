from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .api_views import AppointmentViewSet, TelegramProfileViewSet

router = DefaultRouter()
router.register(r"appointments", AppointmentViewSet, basename="appointment")
router.register(r"profiles", TelegramProfileViewSet, basename="profile")

urlpatterns = [
    path("", include(router.urls)),
]
