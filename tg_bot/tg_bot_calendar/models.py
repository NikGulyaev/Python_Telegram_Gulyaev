from django.db import models  # type: ignore
from django.contrib.auth.models import User  # type: ignore


# Create your models here.
class Event(models.Model):
    name = models.CharField(max_length=255)
    date = models.DateField()
    time = models.TimeField()


class BotStatistics(models.Model):
    date = models.DateField()
    user_count = models.PositiveIntegerField(default=0)
    event_count = models.PositiveIntegerField(default=0)
    edited_events = models.PositiveIntegerField(default=0)
    cancelled_events = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Статистика за {self.date}"


class Appointment(models.Model):
    organizer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="organized_appointments",
        null=True,
        blank=True,
        verbose_name="Организатор",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="appointments",
        verbose_name="Участник",
        )
    event = models.ForeignKey(
        'Event', on_delete=models.CASCADE, null=True, blank=True)
    date = models.DateField()
    time = models.TimeField()
    details = models.TextField(blank=True)
    status = models.CharField(max_length=40)
    telegram_id = models.BigIntegerField(null=True, blank=True)

    def __str__(self):
        return (f"{self.user.username} - {self.event} -"
                f"{self.date} - {self.time}")
