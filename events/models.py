from django.conf import settings
from django.db import models
from django.db.models import Q

# HU-12: límite diario de horas de gestión por organizador.
DEFAULT_DAILY_HOURS_LIMIT = 6
MIN_DAILY_HOURS_LIMIT = 1
MAX_DAILY_HOURS_LIMIT = 16


class Activity(models.Model):
    STATUS_CHOICES = [
        ('active', 'Activo'),
        ('completed', 'Finalizado'),
    ]
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='activities')
    name = models.CharField(max_length=200)
    date_event = models.DateField()
    event_type = models.CharField(max_length=100, blank=True, default='')
    location = models.CharField(max_length=200, blank=True, default='')
    client = models.CharField(max_length=200, blank=True, default='')
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Subtask(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('in_progress', 'En curso'),
        ('done', 'Hecho'),
        ('postponed', 'Pospuesto'),
    ]
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subtasks')
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='subtasks')
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True, default='')
    contact = models.CharField(max_length=200, blank=True, default='')
    description = models.TextField(blank=True, null=True)
    due_date = models.DateField()
    scheduled_date = models.DateField()  # día al que está asignada (para detectar sobrecarga)
    estimated_hours = models.DecimalField(max_digits=4, decimal_places=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class DailyCapacity(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='daily_capacities')
    date = models.DateField()
    total_hours_assigned = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    class Meta:
        unique_together = ('user', 'date')

    def __str__(self):
        return f"{self.user_id} - {self.date}: {self.total_hours_assigned}h"

##modelo para guardar la configuración de cada organizador (límite diario de horas de gestión)
class OrganizerSettings(models.Model):
    """Configuración propia de cada organizador (una fila por usuario).

    Si el usuario nunca configuró nada no existe fila y se usa el valor por
    defecto (ver events.services.get_daily_limit).
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='organizer_settings',
    )
    daily_hours_limit = models.PositiveSmallIntegerField(default=DEFAULT_DAILY_HOURS_LIMIT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(daily_hours_limit__gte=MIN_DAILY_HOURS_LIMIT)
                & Q(daily_hours_limit__lte=MAX_DAILY_HOURS_LIMIT),
                name='daily_hours_limit_between_1_and_16',
            ),
        ]

    def __str__(self):
        return f"{self.user_id}: {self.daily_hours_limit}h/día"
