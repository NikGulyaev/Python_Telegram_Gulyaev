import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot.settings')
import django  # type: ignore
django.setup()


from datetime import date, datetime

from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler
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
    invite_to_event
    )
from tg_bot_calendar.models import Event, Appointment


# Подключение к БД
conn = psycopg2.connect(
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
)

ASK_DATE, ASK_TIME, ASK_PARTICIPANT = range(3)


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
        with self.conn.cursor() as cur:
            insert_query = """
                INSERT INTO events (name, date, time, details, telegram_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
            """
            cur.execute(insert_query, (name, date, time, details, telegram_id))
            row = cur.fetchone()
            self.conn.commit()
            return row[0] if row else None

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


def register(update, context):
    user = update.effective_user
    is_new = calendar.register_user(user.id, user.username, user.first_name)

    if is_new:
        stats = get_or_create_daily_stats()
        stats.user_count += 1
        stats.save()

    update.message.reply_text(
        "✅ Вы зарегистрированы! Теперь можно создавать события."
        )


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


def edit_event_handler(update, context):
    user = update.effective_user
    if not calendar.is_user_registered(user.id):
        update.message.reply_text(
            "❌ Сначала зарегистрируйтесь командой /register.")
        return

    text = update.message.text or ""
    parts = text.split()
    if len(parts) < 6:
        update.message.reply_text(
            "Формат: /edit_event <id> <название>"
            "<дата(YYYY-MM-DD)> <время(HH:MM)> <описание>"
        )
        return

    try:
        event_id = int(parts[1])
    except ValueError:
        update.message.reply_text("ID должен быть целым числом.")
        return

    name = parts[2]
    date = parts[3]
    time = parts[4]
    details = " ".join(parts[5:])

    success = calendar.edit_event(event_id, user.id, name, date, time, details)
    if success:
        # Обновляем статистику
        stats = get_or_create_daily_stats()
        stats.edited_events += 1
        stats.save()

        update.message.reply_text(f"✅ Событие с ID {event_id} обновлено.")
    else:
        update.message.reply_text(
            f"❌ Событие с ID {event_id} не найдено или не принадлежит вам.")


def delete_event_handler(update, context):
    user = update.effective_user
    if not calendar.is_user_registered(user.id):
        update.message.reply_text(
            "❌ Сначала зарегистрируйтесь командой /register.")
        return

    text = update.message.text or ""
    parts = text.split()
    if len(parts) != 2:
        update.message.reply_text("Формат: /delete_event <ID>")
        return

    try:
        event_id = int(parts[1])
    except ValueError:
        update.message.reply_text("ID должен быть целым числом.")
        return

    success = calendar.delete_event(event_id, user.id)
    if success:
        # Обновляем статистику
        stats = get_or_create_daily_stats()
        stats.cancelled_events += 1
        stats.save()

        update.message.reply_text(f"✅ Событие с ID {event_id} удалено.")
    else:
        update.message.reply_text(
            f"❌ Событие с ID {event_id} не найдено или не принадлежит вам.")


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

    text = "📅 Ваши занятые слоты на сегодня:\n"
    for start, end in slots:
        text += f"- {start}\n"

    update.message.reply_text(text)


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


def ask_participant(update, context):
    """Получаем участника и создаём встречу."""
    text = (update.message.text or "").strip()
    user = update.effective_user

    # Организатор — тот, кто запустил команду
    try:
        organizer = (
            User.objects.get(username=user.username)
            if user.username else User.objects.first()
        )
    except User.DoesNotExist:
        organizer = User.objects.first()

    if organizer is None:
        update.message.reply_text(
            "❌ Ваш аккаунт не найден в системе. Обратитесь к администратору."
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Выбор участника: «я» или @username
    if text.lower() == "я":
        participant = organizer
        participant_tg_id = user.id
    else:
        participant_username = text.lstrip("@")
        try:
            participant = User.objects.get(username=participant_username)
        except User.DoesNotExist:
            update.message.reply_text(
                "❌ Пользователь не найден."
                "Проверьте @username и попробуйте снова.\n"
                "Для отмены: /cancel"
            )
            return ASK_PARTICIPANT
        # Telegram ID участника пока неизвестен — оставляем пустым,
        # участник подтвердит встречу сам через /accept
        participant_tg_id = None

    meeting_date = context.user_data.get("meeting_date")
    meeting_time = context.user_data.get("meeting_time")

    # Создаём событие под конкретную дату и время
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
        )
        update.message.reply_text(
            f"✅ Встреча создана!\n"
            f"📅 {meeting_date.strftime('%d.%m.%Y')} в "
            f"{meeting_time.strftime('%H:%M')}\n"
            f"👥 Организатор: {organizer.username}\n"
            f"👥 Участник: {participant.username}\n"
            f"Статус: {appointment.status}."
        )
    except ValidationError as e:
        # Слот участника занят — удаляем ненужное событие
        event.delete()
        update.message.reply_text(
            f"❌ Ошибка: {e.message if hasattr(e, 'message') else e}"
            )
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


def invite_test_handler(update, context):
    user = update.effective_user
    try:
        django_user = (User.objects.get(username=user.username)
                       if user.username else User.objects.first())
    except User.DoesNotExist:
        django_user = User.objects.first()

    # Берём первое событие из базы (или создаём тестовое, если пусто)
    event, _ = Event.objects.get_or_create(
        name="Тестовая встреча",
        defaults={'date': datetime.now().date(),
                  'time': datetime.strptime("14:00", "%H:%M").time()}
    )

    test_date = datetime.now().date()
    test_time = datetime.strptime("14:00", "%H:%M").time()

    try:
        print(f"DEBUG: user.id = {user.id}, user.username = {user.username}")
        invite_to_event(
            organizer_user=django_user,
            participant_user=django_user,  # Для теста участник = организатор
            event=event,
            date=test_date,
            time=test_time,
            details="Тестовая запись",
            participant_telegram_id=user.id
        )
        update.message.reply_text("✅ Встреча создана! Статус: ожидается.")
    except ValidationError as e:
        update.message.reply_text(f"❌ Ошибка: {e}")
    except Exception as e:
        print(e)
        update.message.reply_text("Произошла ошибка при создании встречи.")


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
            "У вас нет приглашений, ожидающих подтверждения.")
        return

    appt = pending.first()
    appt.status = 'подтверждено'
    appt.save()

    update.message.reply_text(f"✅ Вы подтвердили встречу: «{appt.event.name}»"
                              f"на {appt.date} в {appt.time}")


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
    appt.status = 'отменено'
    appt.save()
    update.message.reply_text(f"❌ Вы отклонили встречу: «{appt.event.name}»")


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

    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(CommandHandler('register', register))
    dispatcher.add_handler(CommandHandler(
        'create_event', create_event_handler))
    dispatcher.add_handler(CommandHandler('list_events', list_events_handler))
    dispatcher.add_handler(CommandHandler('get_event', get_event_handler))
    dispatcher.add_handler(CommandHandler('edit_event', edit_event_handler))
    dispatcher.add_handler(CommandHandler(
        'delete_event', delete_event_handler))
    dispatcher.add_handler(CommandHandler('check_slots', check_slots_handler))
    dispatcher.add_handler(CommandHandler('invite_test', invite_test_handler))
    dispatcher.add_handler(CommandHandler('accept',
                                          accept_appointment_handler))
    dispatcher.add_handler(CommandHandler('decline',
                                          decline_appointment_handler))

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
