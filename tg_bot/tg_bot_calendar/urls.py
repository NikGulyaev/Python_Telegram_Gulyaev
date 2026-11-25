from django.urls import path
from . import views

urlpatterns = [
    path("events/export/", views.export_events_csv, name="export_events_csv"),
    path("events/export/json/", views.export_events_json, name="export_events_json"),
]
