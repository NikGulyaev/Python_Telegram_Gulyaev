"""Разовый перенос данных из таблиц бота (users, events) в модели Django."""
import os
import sys

import psycopg2
import django
from dotenv import load_dotenv, find_dotenv

# Настраиваем Django-окружение (скрипт лежит рядом с manage.py)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tg_bot.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from tg_bot_calendar.models import (  # noqa: E402
    Appointment, Event, TelegramProfile, UserActivityStats,
)

load_dotenv(find_dotenv())

conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT", "5433"),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
)
cur = conn.cursor()

# --- 1. Пользователи ---
cur.execute("SELECT telegram_id, username, first_name, registered_at FROM users")
users_raw = cur.fetchall()
print(f"Пользователей в старой таблице: {len(users_raw)}")

telegram_id_to_user = {}
for tg_id, username, first_name, _registered_at in users_raw:
    django_username = username or f"tg_{tg_id}"
    user, created = User.objects.get_or_create(
        username=django_username,
        defaults={"first_name": first_name or ""},
    )
    if created:
        user.set_unusable_password()  # у ботовода свой пароль не нужен
        user.save()
    TelegramProfile.objects.get_or_create(
        telegram_id=tg_id,
        defaults={
            "user": user,
            "username": username,
            "first_name": first_name,
        },
    )
    UserActivityStats.objects.get_or_create(user=user)
    telegram_id_to_user[tg_id] = user
    print(f"  {'создан' if created else 'найден'}: {django_username} (tg {tg_id})")

# --- 2. События ---
cur.execute("SELECT id, name, date, time, details, telegram_id FROM events")
events_raw = cur.fetchall()
print(f"Событий в старой таблице: {len(events_raw)}")

for _id, name, date, time, details, tg_id in events_raw:
    user = telegram_id_to_user.get(tg_id)
    if user is None:
        print(f"  !! у события {name!r} нет пользователя tg_id={tg_id} — пропускаю")
        continue

    # Проверка на повторный запуск: не дублируем то, что уже перенесли
    if Appointment.objects.filter(
        telegram_id=tg_id, date=date, time=time, details=details or ""
    ).exists():
        print(f"  пропуск (уже перенесено): {name} {date} {time}")
        continue

    event = Event.objects.create(name=name, date=date, time=time)
    Appointment.objects.create(
        organizer=user,
        user=user,
        event=event,
        date=date,
        time=time,
        details=details or "",
        status="planned",          # см. примечание ниже
        telegram_id=tg_id,
        is_public=False,
    )
    print(f"  перенесено: {name} {date} {time}")

conn.close()

print("\nИтог в Django-базе:")
print("  Users:", User.objects.count())
print("  TelegramProfile:", TelegramProfile.objects.count())
print("  Event:", Event.objects.count())
print("  Appointment:", Appointment.objects.count())
