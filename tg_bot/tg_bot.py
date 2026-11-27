import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot.settings')
import django  # type: ignore
django.setup()

import logging
import csv
from io import StringIO
from datetime import date, datetime
from telegram import Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
    )
import psycopg2  # type: ignore
from django.contrib.auth.models import User  # type: ignore
from django.core.exceptions import ValidationError  # type: ignore
from psycopg2 import extras
from config import (
    API_TOKEN,
    DB_HOST,
    DB_PORT,
    DB_NAME,
    DB_PASSWORD,
    DB_USER
    )
from tg_bot_calendar.utils import (
    get_or_create_daily_stats,
    get_busy_slots,
    invite_to_event,
    get_django_user_by_telegram_id,
    get_appointments_by_telegram_id,
    can_manage_appointment,
    increment_cancelled,
    increment_edited,
    MEETING_DURATION,
    )
from tg_bot_calendar.models import Event, Appointment, TelegramProfile


# Подключение к БД
conn = psycopg2.connect(
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
)

ASK_DATE, ASK_TIME, ASK_PARTICIPANT, ASK_LOGIN = range(4)

SELECT_APPOINTMENT_TO_DELETE = "select_appointment_to_delete"


DJANGO_BASE_URL = "http://127.0.0.1:8000"


class Calendar:
    def __init__(self, conn):
        self.conn = conn
        self._ensure_tables()  # Создаём таблицы при инициализации

    def _ensure_tables(self):
        """Создаёт таблицы users и events, если они не существуют."""
        with self.conn.cursor() as cur:
            # Таблица users
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    registered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)

            # Таблица events
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    date DATE NOT NULL,
                    time TIME NOT NULL,
                    details TEXT,
                    telegram_id BIGINT NOT NULL,
                    CONSTRAINT fk_user FOREIGN KEY (telegram_id)
                    REFERENCES users(telegram_id) ON DELETE CASCADE
                );
            """)

            # Индекс для быстрого поиска событий по пользователю
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_telegram_id
                ON events(telegram_id);
            """)
            self.conn.commit()

    # Регистрация пользователя в БД
    def register_user(self, telegram_id, username, first_name):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (telegram_id, username, first_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO NOTHING
                RETURNING telegram_id;
            """, (telegram_id, username, first_name))
            row = cur.fetchone()
            self.conn.commit()
            return row is not None  # True — только что создан

    # Проверка, зарегистрирован ли пользователь
    def is_user_registered(self, telegram_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE telegram_id = %s",
                        (telegram_id,))
            return cur.fetchone() is not None

    # Создание события с привязкой к пользователю
    def create_event(self, telegram_id, name, date, time, details):
        profile = TelegramProfile.objects.get(telegram_id=telegram_id)

        # Парсим строки в настоящие date/time — иначе в базу уйдут строки,
        # и strftime('%d.%m.%Y') в /calendar упадёт
        parsed_date = datetime.strptime(date, "%Y-%m-%d").date()
        parsed_time = datetime.strptime(time, "%H:%M").time()

        event = Event.objects.create(
            name=name,
            date=parsed_date,
            time=parsed_time,
            )
        appointment = Appointment.objects.create(
            organizer=profile.user,
            user=profile.user,
            event=event,
            date=parsed_date,
            time=parsed_time,
            details=details,
            status="ожидается",
        )
        return appointment.id

    # Получение событий только для этого пользователя
    def list_events(self, telegram_id):
        with self.conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            query = "SELECT * FROM events WHERE telegram_id = %s ORDER BY id;"
            cur.execute(query, (telegram_id,))
            return cur.fetchall()

    def get_event(self, event_id, telegram_id):
        with self.conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            query = "SELECT * FROM events WHERE id = %s AND telegram_id = %s;"
            cur.execute(query, (event_id, telegram_id))
            return cur.fetchone()

    def edit_event(self, event_id, telegram_id, name=None, date=None,
                   time=None, details=None):
        updates = []
        values = []

        if name is not None:
            updates.append("name = %s")
            values.append(name)
        if date is not None:
            updates.append("date = %s")
            values.append(date)
        if time is not None:
            updates.append("time = %s")
            values.append(time)
        if details is not None:
            updates.append("details = %s")
            values.append(details)

        if not updates:
            return False

        values.extend([event_id, telegram_id])
        set_clause = ", ".join(updates)
        query = (f"UPDATE events SET {set_clause} WHERE id = %s"
                 "AND telegram_id = %s RETURNING id;")

        with self.conn.cursor() as cur:
            cur.execute(query, values)
            row = cur.fetchone()
            self.conn.commit()
            return row is not None

    def delete_event(self, event_id, telegram_id):
        with self.conn.cursor() as cur:
            query = ("DELETE FROM events WHERE id = %s"
                     " AND telegram_id = %s RETURNING id;")
            cur.execute(query, (event_id, telegram_id))
            row = cur.fetchone()
            self.conn.commit()
            return row is not None


calendar = Calendar(conn)


def upsert_profile(django_user, telegram_id, telegram_username, first_name):
    """Создаёт или обновляет TelegramProfile по Django-пользователю.

    Возвращает (profile, created).
    """
    return TelegramProfile.objects.update_or_create(
        user=django_user,
        defaults={
            "telegram_id": telegram_id,
            "username": telegram_username,
            "first_name": first_name,
        },
    )


def start(update, context):
    update.message.reply_text(
        "Привет! Я Бот‑календарь.\n"
        "Сначала зарегистрируйтесь командой /register, затем используйте:\n"
        "/create_event <название> <дата(YYYY-MM-DD)> <время(HH:MM)> <инфо>\n"
        "/list_events\n"
        "/get_event <id>\n"
        "/edit_event <id> <название> <дата> <время> <описание>\n"
        "/delete_event <id>\n"
        "/create_meeting — создать встречу (дата, время, участник)"
    )


def register_start(update, context):
    """Запрашивает логин Django-пользователя."""
    user = update.effective_user
    # Проверяем, есть ли уже профиль — если да, сразу говорим, что всё ок
    try:
        TelegramProfile.objects.get(telegram_id=user.id)
        update.message.reply_text("✅ Ваш профиль уже зарегистрирован.")
        return ConversationHandler.END
    except TelegramProfile.DoesNotExist:
        pass

    update.message.reply_text(
        "Введите логин вашего Django-аккаунта (например, admin):"
    )
    return ASK_LOGIN


def register_save_login(update, context):
    """Сохраняет профиль, привязывая telegram_id к указанному логину."""
    user = update.effective_user
    raw_login = (update.message.text or "").strip()
    login = raw_login.lstrip("@")

    if not login:
        update.message.reply_text("❌ Логин не может быть пустым. Попробуйте ещё раз или /cancel.")
        return ASK_LOGIN

    try:
        django_user = User.objects.get(username=login)
    except User.DoesNotExist:
        update.message.reply_text(f"❌ Пользователь с логином «{login}» не найден."
                                  f" Попробуйте ещё раз или /cancel.")
        return ASK_LOGIN

    profile, created = upsert_profile(
        django_user, user.id, user.username, user.first_name,
    )

    if created:
        msg = "✅ Профиль успешно создан."
    else:
        msg = "🔄 Профиль обновлён."

    update.message.reply_text(msg)
    return ConversationHandler.END


def register_handler(update, context):
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    try:
        django_user = User.objects.get(username=user.username)
    except User.DoesNotExist:
        update.message.reply_text(
            "❌ Ваш аккаунт не найден в системе. Обратитесь к администратору."
            )
        return ConversationHandler.END

    profile, created = upsert_profile(
        django_user, user.id, user.username, user.first_name,
    )

    if created:
        msg = "✅ Вы зарегистрированы в системе."
    else:
        msg = "🔄 Ваш профиль обновлён."

    update.message.reply_text(msg)
    return ConversationHandler.END


def create_event_handler(update, context):
    user = update.effective_user
    if not calendar.is_user_registered(user.id):
        update.message.reply_text(
            "❌ Сначала зарегистрируйтесь командой /register.")
        return

    text = update.message.text or ""
    parts = text.split()

    if len(parts) < 5:
        update.message.reply_text(
            "Формат: /create_event <название> "
            "<дата(YYYY-MM-DD)> <время(HH:MM)> <описание>"
        )
        return

    name = parts[1]
    date = parts[2]
    time = parts[3]
    details = " ".join(parts[4:])

    try:
        event_id = calendar.create_event(user.id, name, date, time, details)
        if event_id is not None:
            # Обновляем статистику в Django
            stats = get_or_create_daily_stats()
            stats.event_count += 1
            stats.save()

            update.message.reply_text(f"✅ Событие создано. ID: {event_id}")
        else:
            update.message.reply_text("❌ Не удалось создать событие.")
    except Exception as e:
        print(e)
        update.message.reply_text("Произошла ошибка при сохранении события.")


def list_events_handler(update, context):
    user = update.effective_user
    if not calendar.is_user_registered(user.id):
        update.message.reply_text(
            "❌ Сначала зарегистрируйтесь командой /register.")
        return

    try:
        events = calendar.list_events(user.id)
        if not events:
            update.message.reply_text("У вас пока нет событий.")
            return

        lines = ["📋 Ваши события:\n"]
        for e in events:
            lines.append(
                f"ID: {e['id']} | {e['name']} ({e['date']} {e['time']})")
            lines.append(f"   📝 {e['details']}\n")

        update.message.reply_text("\n".join(lines))
    except Exception as e:
        print(e)
        update.message.reply_text("Ошибка при получении списка событий.")


def get_event_handler(update, context):
    user = update.effective_user
    if not calendar.is_user_registered(user.id):
        update.message.reply_text(
            "❌ Сначала зарегистрируйтесь командой /register.")
        return

    text = update.message.text or ""
    parts = text.split()
    if len(parts) != 2:
        update.message.reply_text("Формат: /get_event <ID>")
        return

    try:
        event_id = int(parts[1])
    except ValueError:
        update.message.reply_text("ID должен быть числом.")
        return

    event = calendar.get_event(event_id, user.id)
    if not event:
        update.message.reply_text(
            f"Событие с ID {event_id} не найдено (или оно не ваше).")
        return

    update.message.reply_text(
        f"📅 Событие #{event['id']}\n"
        f"Название: {event['name']}\n"
        f"Дата: {event['date']}\n"
        f"Время: {event['time']}\n"
        f"Описание: {event['details']}"
    )


def parse_edit_args(parts):
    """Разбирает аргументы команды /edit_event.

    Возвращает (values_dict, None) или (None, error_message).
    """
    if len(parts) < 6:
        return None, ("Формат: /edit_event <id> <название> <дата(YYYY-MM-DD)> "
                      "<время(HH:MM)> <описание>")
    try:
        appointment_id = int(parts[1])
    except ValueError:
        return None, "ID должен быть целым числом."
    try:
        new_date = datetime.strptime(parts[3], "%Y-%m-%d").date()
        new_time = datetime.strptime(parts[4], "%H:%M").time()
    except ValueError:
        return None, "❌ Неверный формат даты/времени. Пример: 2026-12-01 14:30"

    values = {
        "id": appointment_id,
        "name": parts[2],
        "date": new_date,
        "time": new_time,
        "details": " ".join(parts[5:]),
    }
    return values, None


def find_slot_conflict(participant, new_date, new_time, exclude_id):
    """Ищет пересечение нового слота с существующими встречами участника.

    Возвращает объект Appointment (если есть конфликт) или None.
    """
    new_start = datetime.combine(new_date, new_time)
    new_end = new_start + MEETING_DURATION

    for other in Appointment.objects.filter(
        user=participant,
        date=new_date,
        status__in=["ожидается", "подтверждено"],
    ).exclude(id=exclude_id):
        other_start = datetime.combine(other.date, other.time)
        other_end = other_start + MEETING_DURATION
        if new_start < other_end and new_end > other_start:
            return other
    return None


def format_slot_conflict_message(appt, conflict, new_date):
    start = datetime.combine(conflict.date, conflict.time)
    end = start + MEETING_DURATION
    return (
        f"❌ Участник {appt.user.username} уже занят "
        f"с {start.strftime('%H:%M')} до {end.strftime('%H:%M')}"
        f" {new_date.strftime('%d.%m.%Y')}."
    )


def build_edit_success_message(appointment_id, name, new_date, new_time, details):
    return (
        f"✅ Встреча ID {appointment_id} обновлена:\n"
        f"📅 {new_date.strftime('%d.%m.%Y')} в {new_time.strftime('%H:%M')}\n"
        f"📝 {name} — {details}"
    )


def edit_event_handler(update, context):
    user = update.effective_user
    parts = (update.message.text or "").split()

    # 1. Парсинг аргументов
    values, error = parse_edit_args(parts)
    if error:
        update.message.reply_text(error)
        return

    appointment_id = values["id"]
    new_date, new_time = values["date"], values["time"]

    # 2. Получение встречи
    try:
        appt = Appointment.objects.select_related("event").get(id=appointment_id)
    except Appointment.DoesNotExist:
        update.message.reply_text(f"❌ Встреча с ID {appointment_id} не найдена.")
        return

    # 3. Проверка прав
    if not can_manage_appointment(appt, user.id):
        update.message.reply_text("❌ Вы не можете редактировать чужую встречу.")
        return

    # 4. Проверка пересечения слотов
    if new_date != appt.date or new_time != appt.time:
        conflict = find_slot_conflict(appt.user, new_date, new_time, appt.id)
        if conflict:
            update.message.reply_text(format_slot_conflict_message(appt, conflict, new_date))
            return

    # 5. Применение изменений
    if appt.event:
        appt.event.name = values["name"]
        appt.event.date = new_date
        appt.event.time = new_time
        appt.event.save()

    appt.date = new_date
    appt.time = new_time
    appt.details = values["details"]
    appt.save()

    # 6. Статистика
    editor = get_django_user_by_telegram_id(user.id)
    if editor:
        increment_edited(editor)

    update.message.reply_text(
        build_edit_success_message(
            appointment_id, values["name"], new_date, new_time, values["details"]
        )
    )


def delete_event_handler(update, context):
    user = update.effective_user

    text = update.message.text or ""
    parts = text.split()
    if len(parts) != 2:
        update.message.reply_text("Формат: /delete_event <ID>")
        return

    try:
        appointment_id = int(parts[1])
    except ValueError:
        update.message.reply_text("ID должен быть целым числом.")
        return

    try:
        appt = Appointment.objects.get(id=appointment_id)
    except Appointment.DoesNotExist:
        update.message.reply_text(f"❌ Встреча с ID {appointment_id} не найдена.")
        return

    # Проверка прав
    if not can_manage_appointment(appt, user.id):
        update.message.reply_text("❌ Вы не можете удалять чужую встречу.")
        return

    # Статистика до удаления (потому что после delete объект уже не живой)
    increment_cancelled(appt.user)

    event = appt.event
    appt.delete()
    event.delete()  # событие без встреч нам больше не нужно

    update.message.reply_text(f"✅ Встреча ID {appointment_id} удалена.")


def check_slots_handler(update, context):
    user = update.effective_user

    # Находим Django User по username (для теста)
    try:
        django_user = (User.objects.get(username=user.username)
                       if user.username else User.objects.first())
    except User.DoesNotExist:
        django_user = User.objects.first()  # Фоллбэк для теста

    today = date.today()
    slots = get_busy_slots(django_user, today)

    if not slots:
        update.message.reply_text(
            "На сегодня у вас нет запланированных встреч."
            )
        return

    lines = ["📅 Ваши занятые слоты на сегодня:"]
    for start_time, end_time in slots:
        lines.append(
            f"- {start_time.strftime('%H:%M')} — {end_time.strftime('%H:%M')}"
        )

    update.message.reply_text("\n".join(lines))


def create_meeting_start(update, context):
    """Точка входа: /create_meeting — спрашиваем дату."""
    update.message.reply_text(
        "📅 Давайте создадим встречу!\n\n"
        "Шаг 1 из 2.\n"
        "Введите дату в формате ГГГГ-ММ-ДД (например 2026-12-25).\n"
        "Для отмены: /cancel"
    )
    return ASK_DATE


def ask_date(update, context):
    """Получаем и проверяем дату."""
    text = (update.message.text or "").strip()

    try:
        meeting_date = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        update.message.reply_text(
            "❌ Не понял дату."
            "Введите в формате ГГГГ-ММ-ДД, например 2026-12-25."
        )
        return ASK_DATE

    if meeting_date < date.today():
        update.message.reply_text(
            "❌ Эта дата уже прошла."
            "Введите будущую дату."
            )
        return ASK_DATE

    context.user_data["meeting_date"] = meeting_date

    update.message.reply_text(
        f"✅ Дата принята: {meeting_date.strftime('%d.%m.%Y')}\n\n"
        "Шаг 2 из 2. Введите время в формате ЧЧ:ММ (например 14:30).\n"
        "Для отмены: /cancel"
    )
    return ASK_TIME


def ask_time(update, context):
    """Получаем время и переходим к выбору участника."""
    text = (update.message.text or "").strip()

    try:
        meeting_time = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        update.message.reply_text(
            "❌ Не понял время. Введите в формате ЧЧ:ММ, например 14:30."
        )
        return ASK_TIME

    context.user_data["meeting_time"] = meeting_time

    update.message.reply_text(
        "✅ Время принято.\n\n"
        "👥 Введите @username участника (например @ivan_petrov).\n"
        "Если встреча только для вас — введите: я\n"
        "Для отмены: /cancel"
    )
    return ASK_PARTICIPANT


def resolve_participant(text, organizer, requester_tg_id):
    """Возвращает (participant_user, participant_tg_id, None)
    или (None, None, error_message)"""
    if text.lower() == "я":
        return organizer, requester_tg_id, None

    participant_username = text.lstrip("@")
    try:
        participant = User.objects.get(username=participant_username)
    except User.DoesNotExist:
        return None, None, (
            f"❌ Пользователь с логином «{participant_username}» не найден.\n"
            "Проверьте правильность написания или используйте «я» для себя."
        )

    try:
        profile = TelegramProfile.objects.get(user=participant)
        return participant, profile.telegram_id, None
    except TelegramProfile.DoesNotExist:
        return participant, None, None


def build_meeting_created_message(organizer, participant, meeting_date,
                                  meeting_time, appointment, participant_tg_id):
    msg = (
        "✅ Встреча создана!\n"
        f"📅 {meeting_date.strftime('%d.%m.%Y')} в {meeting_time.strftime('%H:%M')}\n"
        f"👥 Организатор: {organizer.username}\n"
        f"👥 Участник: {participant.username}\n"
        f"📋 Статус: {appointment.status}"
    )
    if participant_tg_id:
        msg += "\n📩 Участнику отправлено приглашение."
    else:
        msg += "\n⚠️ У участника нет профиля — уведомление не отправлено."
    return msg


def ask_participant(update, context):
    text = (update.message.text or "").strip()
    user = update.effective_user

    organizer = get_django_user_by_telegram_id(user.id)
    if organizer is None:
        update.message.reply_text(
            "❌ Ваш профиль не зарегистрирован. "
            "Сначала выполните /register и укажите логин (например, admin)."
        )
        context.user_data.clear()
        return ConversationHandler.END

    participant, participant_tg_id, error = resolve_participant(text, organizer, user.id)
    if error:
        update.message.reply_text(error)
        return ASK_PARTICIPANT

    meeting_date = context.user_data.get("meeting_date")
    meeting_time = context.user_data.get("meeting_time")

    event = Event.objects.create(
        name=f"Встреча: {organizer.username} — {participant.username}",
        date=meeting_date,
        time=meeting_time,
    )

    try:
        appointment = invite_to_event(
            organizer_user=organizer,
            participant_user=participant,
            event=event,
            date=meeting_date,
            time=meeting_time,
            details=f"Приглашение от {organizer.username}",
            participant_telegram_id=participant_tg_id,
            bot=context.bot,
        )

        msg = build_meeting_created_message(
            organizer, participant, meeting_date, meeting_time, appointment, participant_tg_id
        )
        update.message.reply_text(msg)

    except ValidationError as e:
        event.delete()
        error_text = e.message if hasattr(e, 'message') else str(e)
        update.message.reply_text(f"❌ Ошибка: {error_text}")
        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        print(e)
        event.delete()
        update.message.reply_text("Произошла ошибка при создании встречи.")
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data.clear()
    return ConversationHandler.END


def cancel_meeting(update, context):
    update.message.reply_text("❌ Создание встречи отменено.")
    context.user_data.clear()
    return ConversationHandler.END


def accept_appointment_handler(update, context):
    user = update.effective_user
    if not user or not user.id:
        update.message.reply_text("❌ Не удалось получить ваш ID.")
        return

    pending = Appointment.objects.filter(
        telegram_id=user.id,
        status='ожидается'
    ).order_by('-id')

    if not pending.exists():
        update.message.reply_text(
            "У вас нет приглашений, ожидающих подтверждения."
        )
        return

    appt = pending.first()

    # Проверка прав: подтверждать может только участник (или организатор)
    if not can_manage_appointment(appt, user.id):
        update.message.reply_text("❌ Вы не можете подтверждать эту встречу.")
        return

    appt.status = 'подтверждено'
    appt.save()

    update.message.reply_text(
        f"✅ Вы подтвердили встречу: «{appt.event.name}» "
        f"на {appt.date} в {appt.time}"
    )


def decline_appointment_handler(update, context):
    user = update.effective_user
    pending = Appointment.objects.filter(
        telegram_id=user.id,
        status='ожидается'
    ).order_by('-id')

    if not pending.exists():
        update.message.reply_text(
            "У вас нет приглашений, ожидающих подтверждения."
        )
        return

    appt = pending.first()

    # Проверка прав: отклонять может только участник (или организатор)
    if not can_manage_appointment(appt, user.id):
        update.message.reply_text("❌ Вы не можете отменять эту встречу.")
        return

    appt.status = 'отменено'
    appt.save()

    # Учёт статистики: отмену засчитываем тому, кто её совершил (участнику)
    increment_cancelled(appt.user)

    update.message.reply_text(f"❌ Вы отклонили встречу: «{appt.event.name}»")


def calendar_command(update, context):
    user = update.effective_user
    appointments = get_appointments_by_telegram_id(user.id)

    if not appointments.exists():
        update.message.reply_text("🗓 Ваш календарь пуст.")
        return

    lines = ["🗓 Ваши встречи:"]
    for appt in appointments.order_by("date", "time"):
        status_emoji = (
            "✅" if appt.status == "подтверждено"
            else "⏳" if appt.status == "ожидается"
            else "❌"
        )
        lines.append(
            f"{status_emoji} ID {appt.id}: {appt.date.strftime('%d.%m.%Y')} "
            f"в {appt.time.strftime('%H:%M')} — {appt.details}"
        )

    update.message.reply_text("\n".join(lines))


def delete_appointment_confirm(update: Update, context: CallbackContext):
    """Пользователь выбрал встречу для удаления — проверяем права и удаляем."""
    user = update.effective_user
    if not user:
        update.message.reply_text("❌ Не удалось определить пользователя.")
        return ConversationHandler.END

    user_data = context.user_data
    if not user_data:
        update.message.reply_text("❌ Не удалось найти данные о встрече.")
        return ConversationHandler.END

    appointment_id = user_data.get("appointment_id")
    if not appointment_id:
        update.message.reply_text("❌ Не удалось найти встречу для удаления.")
        return ConversationHandler.END

    try:
        appointment = Appointment.objects.get(id=appointment_id)
    except Appointment.DoesNotExist:
        update.message.reply_text("❌ Встреча не найдена (возможно, уже удалена).")
        return ConversationHandler.END

    # --- ГЛАВНОЕ: проверка прав ---
    if not can_manage_appointment(appointment, user.id):
        update.message.reply_text(
            "❌ Вы не можете удалять эту встречу: вы не организатор и не участник."
        )
        return ConversationHandler.END

    appointment.delete()
    update.message.reply_text("✅ Встреча успешно удалена.")
    return ConversationHandler.END


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def error_handler(update, context):
    logger.error("Ошибка при обработке апдейта %s", update, exc_info=context.error)


def share_event_handler(update, context):
    user = update.effective_user
    message = update.effective_message

    text = message.text or ""
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        message.reply_text("Формат: /share <ID> [off]")
        return

    appointment_id = int(parts[1])

    try:
        appt = Appointment.objects.get(id=appointment_id)
    except Appointment.DoesNotExist:
        message.reply_text(f"❌ Встреча с ID {appointment_id} не найдена.")
        return

    # Только владелец может менять публичность
    if not can_manage_appointment(appt, user.id):
        message.reply_text("❌ Вы можете делиться только своими встречами.")
        return

    make_public = not (len(parts) >= 3 and parts[2].lower() == "off")
    appt.is_public = make_public
    appt.save()

    if make_public:
        message.reply_text(
            f"🌍 Встреча ID {appointment_id} теперь публичная: "
            f"«{appt.event.name}» {appt.date.strftime('%d.%m.%Y')} в {appt.time.strftime('%H:%M')}"
        )
    else:
        message.reply_text(f"🔒 Встреча ID {appointment_id} снова приватная.")


def shared_events_handler(update, context):
    message = update.effective_message

    # Получаем публичные встречи, сортируем по дате.
    public_appointments = Appointment.objects.filter(
        is_public=True,
        status__in=["подтверждено", "ожидается"]
    ).order_by("date", "time")

    if not public_appointments.exists():
        message.reply_text("🌍 Сейчас нет публичных событий.")
        return
    lines = ["📋 Общие (публичные) события:"]
    for appt in public_appointments:
        # Показываем организатора, чтобы знать кто поделился
        if appt.organizer and appt.organizer.username:
            organizer_name = appt.organizer.username
        elif appt.organizer:
            organizer_name = appt.organizer.first_name or f"пользователь #{appt.organizer_id}"
        else:
            organizer_name = f"не указан (tg {appt.telegram_id or '—'})"
        status_emoji = "✅" if appt.status == "подтверждено" else "⏳"

        lines.append(
            f"{status_emoji} ID {appt.id}: «{appt.event.name}» "
            f"на {appt.date.strftime('%d.%m.%Y')} в {appt.time.strftime('%H:%M')}"
            f"(от {organizer_name})"
        )

    message.reply_text("\n".join(lines))


def export_events_handler(update, context):
    message = update.effective_message
    user = update.effective_user

    # Получаем только свои встречи
    appointments = get_appointments_by_telegram_id(user.id)
    if not appointments.exists():
        message.reply_text("📄 У вас нет событий для выгрузки.")
        return

    # Готовим CSV в памяти
    output = StringIO()
    writer = csv.writer(output)

    # Заголовки
    writer.writerow([
        "ID", "Название", "Дата", "Время", "Детали", "Статус",
        "Организатор", "Участник (Telegram ID)", "Публичное"
    ])

    # Заполняем строки
    for appt in appointments:
        # Безопасное имя организатора (как мы уже научились)
        if appt.organizer and appt.organizer.username:
            organizer_name = appt.organizer.username
        elif appt.organizer:
            organizer_name = appt.organizer.first_name or f"пользователь #{appt.organizer_id}"
        else:
            organizer_name = "не указан"

        writer.writerow([
            appt.id,
            appt.event.name,
            appt.date.strftime("%Y-%m-%d"),
            appt.time.strftime("%H:%M"),
            appt.details,
            appt.status,
            organizer_name,
            user.id,
            "Да" if appt.is_public else "Нет"
        ])

    csv_data = output.getvalue()
    output.close()

    # Отправляем файл в чат
    context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=csv_data.encode("utf-8"),
        filename="my_events.csv",
        caption="📥 Ваша выгрузка событий (CSV)"
    )


def export_link_handler(update, context):
    message = update.effective_message
    user = update.effective_user

    try:
        TelegramProfile.objects.get(telegram_id=user.id)
    except TelegramProfile.DoesNotExist:
        message.reply_text("❌ Сначала зарегистрируйтесь командой /register.")
        return

    csv_url = f"{DJANGO_BASE_URL}/events/export/?telegram_id={user.id}"
    json_url = f"{DJANGO_BASE_URL}/events/export/json/?telegram_id={user.id}"

    message.reply_text(
        "📥 Ваши события для выгрузки:\n\n"
        f"CSV: {csv_url}\n"
        f"JSON: {json_url}\n\n"
        "Откройте ссылку в браузере — файл скачается автоматически."
    )


def main():
    updater = Updater(token=API_TOKEN)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("create_meeting", create_meeting_start)],
        states={
            ASK_DATE: [MessageHandler(Filters.text & ~Filters.command,
                                      ask_date)],
            ASK_TIME: [MessageHandler(Filters.text & ~Filters.command,
                                      ask_time)],
            ASK_PARTICIPANT: [MessageHandler(Filters.text & ~Filters.command,
                                             ask_participant)]
        },
        fallbacks=[CommandHandler("cancel", cancel_meeting)],
    )
    dispatcher.add_handler(conv_handler)

    register_conv = ConversationHandler(
        entry_points=[CommandHandler("register", register_start)],
        states={
            ASK_LOGIN: [
                MessageHandler(Filters.text & ~Filters.command, register_save_login)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_meeting)],  # важно: используй тот же cancel,
                                                               # что и у create_meeting
    )
    dispatcher.add_handler(register_conv)

    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(CommandHandler(
        'create_event', create_event_handler))
    dispatcher.add_handler(CommandHandler('list_events', list_events_handler))
    dispatcher.add_handler(CommandHandler('get_event', get_event_handler))
    dispatcher.add_handler(CommandHandler('edit_event', edit_event_handler))
    dispatcher.add_handler(CommandHandler(
        'delete_event', delete_event_handler))
    dispatcher.add_handler(CommandHandler('check_slots', check_slots_handler))
    dispatcher.add_handler(CommandHandler('accept',
                                          accept_appointment_handler))
    dispatcher.add_handler(CommandHandler('decline',
                                          decline_appointment_handler))
    dispatcher.add_handler(CommandHandler("calendar", calendar_command))
    dispatcher.add_handler(CommandHandler("share", share_event_handler))
    dispatcher.add_handler(CommandHandler("shared", shared_events_handler))
    dispatcher.add_handler(CommandHandler("export_link", export_link_handler))
    dispatcher.add_handler(CommandHandler("export", export_events_handler))
    dispatcher.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
