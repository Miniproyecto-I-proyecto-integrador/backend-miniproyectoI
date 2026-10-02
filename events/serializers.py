from rest_framework import serializers
from django.db.models import Sum
from .models import Activity, Subtask, DailyCapacity


class SubtaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subtask
        fields = '__all__'
        read_only_fields = ('user',)

    def validate(self, data):
        activity = data.get('activity', self.instance.activity if self.instance else None)
        scheduled_date = data.get('scheduled_date', self.instance.scheduled_date if self.instance else None)
        due_date = data.get('due_date', self.instance.due_date if self.instance else None)
        estimated_hours = data.get('estimated_hours', self.instance.estimated_hours if self.instance else None)

        if activity:
            request = self.context.get('request')
            if not request or activity.user_id != request.user.pk:
                raise serializers.ValidationError({
                    'activity': 'Solo puedes asociar subtareas a tus propios eventos.'
                })

            # 1. Validación de fechas frente a la fecha del evento
            if scheduled_date and scheduled_date > activity.date_event:
                raise serializers.ValidationError({
                    "scheduled_date": "La fecha programada no puede ser posterior a la fecha del evento."
                })
                
            if due_date and due_date > activity.date_event:
                raise serializers.ValidationError({
                    "due_date": "La fecha límite no puede ser posterior a la fecha del evento."
                })

            # 2. Horas estimadas positivas
            if estimated_hours is not None and estimated_hours <= 0:
                raise serializers.ValidationError({
                    "estimated_hours": "Las horas estimadas deben ser mayores a 0."
                })

            # 3. Lógica de sobrecarga diaria (Límite 6h)
            if scheduled_date and estimated_hours:
                existing_subtasks = Subtask.objects.filter(
                    user_id=activity.user_id,
                    activity__user_id=activity.user_id,
                    scheduled_date=scheduled_date
                )
                
                if self.instance:
                    existing_subtasks = existing_subtasks.exclude(id=self.instance.id)

                current_hours = existing_subtasks.aggregate(Sum('estimated_hours'))['estimated_hours__sum'] or 0
                total_hours = float(current_hours) + float(estimated_hours)
                limit = 6.0 

                if total_hours > limit:
                    raise serializers.ValidationError({
                        "conflicto": f"Quedarías con {total_hours}h de gestión planificadas para el {scheduled_date} (límite {limit}h)."
                    })

        return data


class ActivitySerializer(serializers.ModelSerializer):
    subtasks = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    completed_subtasks = serializers.SerializerMethodField()
    total_subtasks = serializers.SerializerMethodField()

    def get_subtasks(self, obj):
        subtasks = getattr(obj, '_owned_subtasks', None)
        if subtasks is None:
            subtasks = obj.subtasks.filter(user=obj.user)
        return SubtaskSerializer(subtasks, many=True, context=self.context).data

    def _get_subtasks(self, obj):
        subtasks = getattr(obj, '_owned_subtasks', None)
        return subtasks if subtasks is not None else obj.subtasks.filter(user=obj.user)

    def get_total_subtasks(self, obj):
        return len(self._get_subtasks(obj))

    def get_completed_subtasks(self, obj):
        return sum(subtask.status == 'done' for subtask in self._get_subtasks(obj))

    def get_progress(self, obj):
        total = self.get_total_subtasks(obj)
        return round(self.get_completed_subtasks(obj) / total * 100) if total else 0

    class Meta:
        model = Activity
        fields = '__all__'
        read_only_fields = ('user',)


class DailyCapacitySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyCapacity
        fields = '__all__'
        read_only_fields = ('user',)