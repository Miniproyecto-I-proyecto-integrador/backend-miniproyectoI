from django.contrib import admin
from .models import Activity, Subtask, DailyCapacity, OrganizerSettings

admin.site.register(Activity)
admin.site.register(Subtask)
admin.site.register(DailyCapacity)
admin.site.register(OrganizerSettings)
