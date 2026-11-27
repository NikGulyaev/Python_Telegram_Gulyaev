from unittest.mock import MagicMock

import datetime

import pytest
from django.contrib.auth.models import User

from bot_loader import bot
from tg_bot_calendar.models import Appointment, TelegramProfile

pytestmark = pytest.mark.django_db


def make_update(user_id, text="/start", first_name="Тест"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = first_name
    update.effective_user.username = "tester"
    update.message.text = text
    update.message.reply_text = MagicMock()
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    return context


def make_profile(telegram_id):
    user = User.objects.create_user(username=f"tg_{telegram_id}", password="pw123456")
    return TelegramProfile.objects.create(user=user, telegram_id=telegram_id)


def test_start_greets():
    update = make_update(111222333)
    bot.start(update, make_context())
    update.message.reply_text.assert_called()


def test_calendar_shows_appointments():
    profile = make_profile(444555666)
    Appointment.objects.create(
        user=profile.user,
        date=datetime.date(2026, 12, 1),
        time=datetime.time(15, 0),
        status="ожидается",
    )
    update = make_update(444555666, text="/calendar")
    bot.calendar_command(update, make_context())

    update.message.reply_text.assert_called()
    args, _ = update.message.reply_text.call_args
    # в ответе должна мелькнуть встреча (дата/время/статус — сверим с фактом)
    text = str(args[0])
    assert ("01.12.2026" in text) or ("15:00" in text) or ("ожидается" in text)


def test_calendar_empty():
    make_profile(777888999)
    update = make_update(777888999, text="/calendar")
    bot.calendar_command(update, make_context())

    update.message.reply_text.assert_called()


def test_create_event_handler_asks_date():
    profile = make_profile(555666777)
    update = make_update(555666777, text="/create_event")
    bot.create_event_handler(update, make_context())
    update.message.reply_text.assert_called()
