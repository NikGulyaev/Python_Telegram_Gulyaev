from datetime import datetime, timedelta
from django.db import transaction
from django.utils import timezone  # type: ignore
from django.core.exceptions import ValidationError  # type: ignore
from telegram import Bot
from .models import (
    Appointment,
    BotStatistics,
    TelegramProfile,
    UserActivityStats
    )


MEETING_DURATION = timedelta(hours=1)


def get_or_create_daily_stats():
    today = timezone.now().date()
    stats, created = BotStatistics.objects.get_or_create(
        date=today,
        defaults={
            'user_count': 0,
            'event_count': 0,
            'edited_events': 0,
            'cancelled_events': 0,
        }
    )
    return stats


def get_user_appointments(user):
    """
    Возвращает все встречи конкретного пользователя.
    """
    return Appointment.objects.filter(user=user)


def get_busy_slots(user, target_date):
    """
    Возвращает список кортежей (start_time, end_time) для всех подтверждённых
    или ожидающих встреч пользователя на указанную дату.
    Длительность встречи — 1 час.
    """
    appointments = Appointment.objects.filter(
        user=user,
        date=target_date,
        status__in=["ожидается", "подтверждено"],
    )

    slots = []
    for appt in appointments:
        # Начало: время встречи
        start_time = appt.time

        # Конец: +1 час к дате+времени встречи
        end_datetime = datetime.combine(target_date, start_time) + timedelta(hours=1)
        end_time = end_datetime.time()

        slots.append((start_time, end_time))

    return slots


def invite_to_event(
    organizer_user,
    participant_user,
    event,
    date,
    time,
    details="",
    participant_telegram_id=None,
    bot=None,
):
    """
    Приглашает пользователя на событие.
    1. Проверяет пересечение интервалов занятости участника.
    2. Если свободно — создаёт встречу со статусом 'ожидается'.
    3. Отправляет участнику уведомление, если у него есть профиль.
    """
    # 1. Проверка занятости: пересечение интервалов, отменённые не считаем
    new_start = datetime.combine(date, time)
    new_end = new_start + MEETING_DURATION

    for appt in Appointment.objects.filter(
        user=participant_user,
        date=date,
        status__in=["ожидается", "подтверждено"],
    ):
        appt_start = datetime.combine(appt.date, appt.time)
        appt_end = appt_start + MEETING_DURATION
        if new_start < appt_end and new_end > appt_start:
            raise ValidationError(
                f"Пользователь {participant_user.username} занят "
                f"с {appt_start.strftime('%H:%M')} "
                f"до {appt_end.strftime('%H:%M')}!"
            )

    # 2. Создаём встречу. telegram_id берём из профиля, если он есть
    telegram_id = participant_telegram_id
    profile = None
    try:
        profile = TelegramProfile.objects.get(user=participant_user)
        telegram_id = profile.telegram_id
    except TelegramProfile.DoesNotExist:
        pass

    appointment = Appointment.objects.create(
        organizer=organizer_user,
        user=participant_user,
        event=event,
        date=date,
        time=time,
        details=details,
        status="ожидается",
        telegram_id=telegram_id,
    )

    # 3. Уведомление участнику (если он регистрировался и мы можем написать)
    if bot and profile:
        notify_participant_about_invitation(
            bot=bot,
            participant_profile=profile,   # ✅ исправлено: имя параметра совпадает с сигнатурой
            event_name=event.name,
            date=date,
            time=time,
        )

    increment_created(organizer_user)

    return appointment


def notify_participant_about_invitation(
    bot: Bot,
    participant_profile: TelegramProfile,
    event_name: str,
    date,
    time,
):
    """
    Отправляет участнику сообщение о приглашении.
    """
    text = (
        f"📅 Вас пригласили на встречу:\n\n"
        f"«{event_name}»\n"
        f"🗓 Дата: {date.strftime('%d.%m.%Y')}\n"
        f"⏰ Время: {time.strftime('%H:%M')}\n\n"
        f"Чтобы подтвердить встречу, нажмите /accept\n"
        f"Чтобы отклонить — /decline"
    )
    try:
        bot.send_message(chat_id=participant_profile.telegram_id, text=text)
        return True
    except Exception as e:
        # Сюда можно добавить логирование
        print(f"Не удалось отправить уведомление: {e}")
        return False


def get_django_user_by_telegram_id(telegram_id):
    try:
        profile = TelegramProfile.objects.get(telegram_id=telegram_id)
        return profile.user
    except TelegramProfile.DoesNotExist:
        return None


@transaction.atomic
def increment_created(user):
    """Увеличивает счетчик созданных событий у пользователя"""
    stats, _ = UserActivityStats.objects.get_or_create(user=user)
    stats.created_count += 1
    stats.save()


@transaction.atomic
def increment_edited(user):
    """Увеличивает счетчик редактирований"""
    stats, _ = UserActivityStats.objects.get_or_create(user=user)
    stats.edited_count += 1
    stats.save()


@transaction.atomic
def increment_cancelled(user):
    """Увеличивает счетчик отмен"""
    stats, _ = UserActivityStats.objects.get_or_create(user=user)
    stats.cancelled_count += 1
    stats.save()


def get_appointments_by_telegram_id(telegram_id: int):
    """Возвращает все встречи пользователя по его telegram_id."""
    try:
        profile = TelegramProfile.objects.get(telegram_id=telegram_id)
        return Appointment.objects.filter(user=profile.user)
    except TelegramProfile.DoesNotExist:
        return Appointment.objects.none()


def can_manage_appointment(appointment: Appointment, telegram_id: int) -> bool:
    """Проверяет, может ли пользователь с данным telegram_id управлять встречей."""
    try:
        profile = TelegramProfile.objects.get(telegram_id=telegram_id)
        user = profile.user
        # Пользователь может управлять, если он организатор ИЛИ участник
        return appointment.organizer == user or appointment.user == user
    except TelegramProfile.DoesNotExist:
        return False
