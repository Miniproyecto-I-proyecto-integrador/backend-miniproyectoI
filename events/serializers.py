from rest_framework import serializers
from django.db.models import Sum
from .models import Activity, Subtask, DailyCapacity


class SubtaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subtask
        fields = '__all__'

    def validate(self, data):
        activity = data.get('activity', self.instance.activity if self.instance else None)
        scheduled_date = data.get('scheduled_date', self.instance.scheduled_date if self.instance else None)
        due_date = data.get('due_date', self.instance.due_date if self.instance else None)
        estimated_hours = data.get('estimated_hours', self.instance.estimated_hours if self.instance else None)

        if activity:
            request = self.context.get('request')
            if request and str(activity.user_id) != str(request.user.pk):
                raise serializers.ValidationError({
                    'activity': 'Solo puedes asociar gestiones a tus propios eventos.'
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


class TodaySubtaskSerializer(serializers.ModelSerializer):
    event_id = serializers.IntegerField(source='activity_id', read_only=True)
    event_name = serializers.CharField(source='activity.name', read_only=True)

    class Meta:
        model = Subtask
        fields = (
            'id',
            'activity',
            'event_id',
            'event_name',
            'name',
            'category',
            'contact',
            'description',
            'due_date',
            'scheduled_date',
            'estimated_hours',
            'status',
            'note',
            'created_at',
        )


class TodayGroupsSerializer(serializers.Serializer):
    vencidas = TodaySubtaskSerializer(many=True)
    para_hoy = TodaySubtaskSerializer(many=True)
    proximas = TodaySubtaskSerializer(many=True)


class TodayFiltersSerializer(serializers.Serializer):
    curso = serializers.CharField(allow_null=True)
    estado = serializers.CharField(allow_null=True)


class TodayActivitiesResponseSerializer(serializers.Serializer):
    fecha = serializers.DateField()
    filtros = TodayFiltersSerializer()
    grupos = TodayGroupsSerializer()
    total = serializers.IntegerField()


class ActivitySerializer(serializers.ModelSerializer):
    subtasks = SubtaskSerializer(many=True, read_only=True)
    progress = serializers.SerializerMethodField()
    completed_subtasks = serializers.SerializerMethodField()
    total_subtasks = serializers.SerializerMethodField()

    def get_total_subtasks(self, obj):
        return obj.subtasks.count()

    def get_completed_subtasks(self, obj):
        return obj.subtasks.filter(status='done').count()

    def get_progress(self, obj):
        total = self.get_total_subtasks(obj)
        return round(self.get_completed_subtasks(obj) / total * 100) if total else 0

    class Meta:
        model = Activity
        fields = '__all__'
        read_only_fields = ('user_id',)


class DailyCapacitySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyCapacity
        fields = '__all__'
        read_only_fields = ('user_id',)