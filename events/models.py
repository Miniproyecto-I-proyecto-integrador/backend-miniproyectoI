from django.db import models
import uuid

# Create your models here.
from django.db import models
import uuid

class Activity(models.Model):
    STATUS_CHOICES = [
        ('active', 'Activo'),
        ('completed', 'Finalizado'),
    ]
    id = models.AutoField(primary_key=True)
    user_id = models.UUIDField()  # referencia al usuario (auth.users de Supabase)
    name = models.CharField(max_length=200)
    date_event = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Subtask(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('done', 'Hecho'),
        ('postponed', 'Pospuesto'),
    ]
    id = models.AutoField(primary_key=True)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='subtasks')
    name = models.CharField(max_length=200)
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
    user_id = models.UUIDField()
    date = models.DateField()
    total_hours_assigned = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    class Meta:
        unique_together = ('user_id', 'date')

    def __str__(self):
        return f"{self.user_id} - {self.date}: {self.total_hours_assigned}h"