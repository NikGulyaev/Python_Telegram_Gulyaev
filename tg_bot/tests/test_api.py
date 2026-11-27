import datetime

import pytest
from django.contrib.auth.models import User

from tg_bot_calendar.models import Appointment, TelegramProfile

pytestmark = pytest.mark.django_db


def _make_user_with_profile(username="api_user", telegram_id=123):
    user = User.objects.create_user(username=username, password="pw123456")
    TelegramProfile.objects.create(
        user=user, telegram_id=telegram_id,
        username=username, first_name="Тест",
    )
    return user


def _make_appointment(user):
    return Appointment.objects.create(
        user=user,
        date=datetime.date(2026, 12, 1),
        time=datetime.time(15, 0),
        status="ожидается",
    )


def test_api_lists_profiles(api_client):
    _make_user_with_profile(username="profile_user", telegram_id=555)

    response = api_client.get("/api/profiles/")

    assert response.status_code == 200
    assert b'"telegram_id":555' in response.content


def test_api_404_on_unknown(api_client):
    assert api_client.get("/api/nonexistent/").status_code == 404


def test_api_lists_public_appointments_by_telegram_id(api_client):
    user = _make_user_with_profile()
    Appointment.objects.create(
        user=user,
        date=datetime.date(2026, 12, 1),
        time=datetime.time(15, 0),
        status="ожидается",
        telegram_id=123,
        is_public=True,
    )

    response = api_client.get("/api/appointments/?telegram_id=123")

    assert response.status_code == 200
    assert str(response.content) != "b'[]'"  # список не пустой
    assert "2026-12-01" in str(response.content)


def test_api_hides_non_public_appointments(api_client):
    # Приватная встреча даже по правильному telegram_id не видна
    user = _make_user_with_profile()
    Appointment.objects.create(
        user=user,
        date=datetime.date(2026, 12, 1),
        time=datetime.time(15, 0),
        status="ожидается",
        telegram_id=123,
        is_public=False,
    )

    response = api_client.get("/api/appointments/?telegram_id=123")

    assert response.status_code == 200
    assert response.content == b"[]"


def test_api_requires_telegram_id_filter(api_client):
    # Без параметра — пусто, даже если публичные записи есть
    user = _make_user_with_profile()
    Appointment.objects.create(
        user=user,
        date=datetime.date(2026, 12, 1),
        time=datetime.time(15, 0),
        status="ожидается",
        telegram_id=123,
        is_public=True,
    )

    response = api_client.get("/api/appointments/")

    assert response.status_code == 200
    assert response.content == b"[]"
