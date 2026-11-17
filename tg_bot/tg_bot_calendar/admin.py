from django.contrib import admin  # type: ignore
from tg_bot_calendar.models import Appointment, BotStatistics, Event


@admin.register(BotStatistics)
class BotStatisticsAdmin(admin.ModelAdmin):
    list_display = (
        'date', 'user_count', 'event_count',
        'edited_events', 'cancelled_events'
        )
    list_filter = ('date',)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        'organizer', 'user', 'event', 'date', 'time', 'status'
        )
    list_filter = ('status', 'date')
    search_fields = ("organizer__username", "user__username")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'time')
