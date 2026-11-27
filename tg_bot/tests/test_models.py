import datetime

import pytest
from django.contrib.auth.models import User

from tg_bot_calendar.models import (
    Appointment, BotStatistics, Event, TelegramProfile, UserActivityStats,
)

pytestmark = pytest.mark.django_db


def test_create_event():
    event = Event.objects.create(
        name="Тестовая встреча",
        date=datetime.date(2026, 12, 1),
        time=datetime.time(15, 0),
    )
    assert Event.objects.filter(name="Тестовая встреча").exists()
    assert "Тестовая встреча" in str(event) and "2026-12-01" in str(event)


def test_create_appointment():
    user = User.objects.create_user(username="tester", password="pw123456")
    event = Event.objects.create(
        name="Встреча для записи",
        date=datetime.date(2026, 12, 1),
        time=datetime.time(15, 0),
    )
    appt = Appointment.objects.create(
        user=user,
        event=event,
        date=datetime.date(2026, 12, 1),
        time=datetime.time(15, 0),
        details="Обсуждаем проект",
        status="ожидается",
        telegram_id=123456789,
        is_public=False,
    )
    assert appt.user == user
    assert appt.event == event
    assert "tester" in str(appt)


def test_appointment_without_event():
    user = User.objects.create_user(username="solo", password="pw123456")
    appt = Appointment.objects.create(
        user=user,
        date=datetime.date(2026, 12, 2),
        time=datetime.time(10, 30),
        status="ожидается",
    )
    assert appt.event is None
    assert appt.is_public is False  # default работает


def test_profile_linking():
    user = User.objects.create_user(username="tg_user", password="pw123456")
    profile = TelegramProfile.objects.create(
        user=user, telegram_id=987654321,
        username="tg_user", first_name="Николай",
    )
    assert profile.user == user
    assert str(profile) == "tg_user (987654321)"
    # unique-поле: второй профиль с тем же telegram_id невозможен
    other = User.objects.create_user(username="other", password="pw123456")
    with pytest.raises(Exception):
        TelegramProfile.objects.create(user=other, telegram_id=987654321)


def test_statistics_counters():
    stats = BotStatistics.objects.create(date=datetime.date(2026, 12, 1))
    assert stats.user_count == 0 and stats.event_count == 0
    stats.user_count = 5
    stats.save()
    stats.refresh_from_db()
    assert stats.user_count == 5


def test_user_activity_stats():
    user = User.objects.create_user(username="active", password="pw123456")
    stats = UserActivityStats.objects.create(user=user)
    assert stats.created_count == 0
    assert str(stats) == "Stats for active"
