from django.contrib import admin  # type: ignore
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from tg_bot_calendar.models import (
    Appointment,
    BotStatistics,
    Event,
    TelegramProfile,
    UserActivityStats
)


class UserActivityStatsInline(admin.StackedInline):
    model = UserActivityStats
    can_delete = False


class AppointmentInline(admin.TabularInline):
    model = Appointment
    fk_name = 'user'
    extra = 0
    readonly_fields = ('status', 'date', 'time', 'details')


class TelegramProfileInline(admin.StackedInline):
    model = TelegramProfile
    extra = 0
    readonly_fields = ('telegram_id', 'user')


class CustomUserAdmin(BaseUserAdmin):
    inlines = [
        TelegramProfileInline,
        UserActivityStatsInline,
        AppointmentInline
        ]


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


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


@admin.register(TelegramProfile)
class TelegramProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "telegram_id", "username", "first_name")
    search_fields = ("user__username", "username")
