from django.contrib import admin
from .models import Activity, Subtask, DailyCapacity

admin.site.register(Activity)
admin.site.register(Subtask)
admin.site.register(DailyCapacity)