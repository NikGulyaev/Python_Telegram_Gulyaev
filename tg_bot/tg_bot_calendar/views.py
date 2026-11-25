from django.shortcuts import render

# Create your views here.
import csv
from io import StringIO
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from .models import Appointment, TelegramProfile


def _get_appointments_for(telegram_id: int):
    """Все встречи пользователя по его telegram_id."""
    try:
        profile = TelegramProfile.objects.get(telegram_id=telegram_id)
    except TelegramProfile.DoesNotExist:
        return None  # пользователь не найден
    # Свои встречи: он участник или организатор
    return Appointment.objects.filter(user=profile.user) | Appointment.objects.filter(organizer=profile.user)


@require_GET
def export_events_csv(request):
    """GET /events/export/?telegram_id=... — CSV-файл со встречами пользователя."""
    telegram_id = request.GET.get("telegram_id")
    if not telegram_id or not telegram_id.isdigit():
        return JsonResponse({"error": "Укажите telegram_id"}, status=400)

    appointments = _get_appointments_for(int(telegram_id))
    if appointments is None:
        return JsonResponse({"error": "Пользователь не найден"}, status=404)

    # Готовим CSV в памяти
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Название", "Дата", "Время", "Детали", "Статус", "Публичное"])

    for appt in appointments.order_by("date", "time"):
        writer.writerow([
            appt.id,
            appt.event.name,
            appt.date.strftime("%Y-%m-%d"),
            appt.time.strftime("%H:%M"),
            appt.details,
            appt.status,
            "Да" if appt.is_public else "Нет",
        ])

    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="my_events.csv"'
    return response


@require_GET
def export_events_json(request):
    """GET /events/export/json/?telegram_id=... — JSON со встречами пользователя."""
    telegram_id = request.GET.get("telegram_id")
    if not telegram_id or not telegram_id.isdigit():
        return JsonResponse({"error": "Укажите telegram_id"}, status=400)

    appointments = _get_appointments_for(int(telegram_id))
    if appointments is None:
        return JsonResponse({"error": "Пользователь не найден"}, status=404)

    data = [
        {
            "id": appt.id,
            "name": appt.event.name,
            "date": appt.date.strftime("%Y-%m-%d"),
            "time": appt.time.strftime("%H:%M"),
            "details": appt.details,
            "status": appt.status,
            "is_public": appt.is_public,
        }
        for appt in appointments.order_by("date", "time")
    ]
    return JsonResponse({"events": data})