from telegram.ext import Updater, CommandHandler
import psycopg2
from psycopg2 import extras

from config import API_TOKEN

# Подключение к БД
conn = psycopg2.connect(
    host="localhost",
    port=5433,
    database="bot_calendar",
    user="bot_calendar",
    password="gn68630s"
)


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
                ON CONFLICT (telegram_id) DO NOTHING;
            """, (telegram_id, username, first_name))
            self.conn.commit()

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
        "/delete_event <id>"
    )


def register(update, context):
    user = update.effective_user
    telegram_id = user.id
    username = user.username
    first_name = user.first_name

    calendar.register_user(telegram_id, username, first_name)

    if calendar.is_user_registered(telegram_id):
        update.message.reply_text(
            "✅ Вы успешно зарегистрированы! Теперь можно создавать события.")
    else:
        update.message.reply_text(
            "⚠️ Произошла ошибка регистрации. Попробуйте позже.")


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
        update.message.reply_text(f"✅ Событие с ID {event_id} удалено.")
    else:
        update.message.reply_text(
            f"❌ Событие с ID {event_id} не найдено или не принадлежит вам.")


def main():
    updater = Updater(token=API_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(CommandHandler('register', register))
    dispatcher.add_handler(CommandHandler(
        'create_event', create_event_handler))
    dispatcher.add_handler(CommandHandler('list_events', list_events_handler))
    dispatcher.add_handler(CommandHandler('get_event', get_event_handler))
    dispatcher.add_handler(CommandHandler('edit_event', edit_event_handler))
    dispatcher.add_handler(CommandHandler(
        'delete_event', delete_event_handler))

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
