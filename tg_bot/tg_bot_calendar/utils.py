# bot_stats/utils.py
from django.utils import timezone  # type: ignore
from django.core.exceptions import ValidationError  # type: ignore
from .models import Appointment, BotStatistics


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
    Возвращает список кортежей (время начала, время конца) для данного
    пользователя на указанную дату. Для простоты считаем,
    что каждая встреча длится 1 час.
    """
    appointments = Appointment.objects.filter(
        user=user,
        date=target_date
    )

    slots = []
    for appt in appointments:
        start_time = appt.time
        end_time = start_time
        slots.append((start_time, end_time))

    return slots


def invite_to_event(
        organizer_user, participant_user, event, date, time,
        details="", participant_telegram_id=None
        ):
    """
    Приглашает пользователя на событие.
    1. Проверяет, нет ли у участника встречи на эту дату и время.
    2. Если свободно — создаёт встречу со статусом 'ожидается'.
    3. Если занято — выбрасывает ошибку.
    """
    # 1. Проверка занятости: ищем встречи участника на эту дату и время
    existing = Appointment.objects.filter(
        user=participant_user,
        date=date,
        time=time
    )

    if existing.exists():
        raise ValidationError(f"Пользователь {participant_user.username}"
                              f" уже занят в это время!")

    # 2. Создаём встречу
    appointment = Appointment.objects.create(
        organizer=organizer_user,
        user=participant_user,
        event=event,
        date=date,
        time=time,
        details=details,
        status='ожидается',
        telegram_id=participant_telegram_id
    )
    return appointment
