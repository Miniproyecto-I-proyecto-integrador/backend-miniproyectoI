from decimal import Decimal
from django.db.models import Max
from rest_framework import serializers
from .models import (
    Activity, Subtask, DailyCapacity,
    MIN_DAILY_HOURS_LIMIT, MAX_DAILY_HOURS_LIMIT,
)
from .services import PLANNED_STATUSES, OverloadConflict, check_day_capacity


class SubtaskSerializer(serializers.ModelSerializer):
    due_date = serializers.DateField(required=False)
    scheduled_date = serializers.DateField(required=False)

    class Meta:
        model = Subtask
        fields = '__all__'
        read_only_fields = ('user',)

    def validate(self, data):
        activity = data.get('activity', self.instance.activity if self.instance else None)
        scheduled_date = data.get(
            'scheduled_date',
            data.get(
                'due_date',
                self.instance.scheduled_date if self.instance else None,
            ),
        )
        estimated_hours = data.get('estimated_hours', self.instance.estimated_hours if self.instance else None)
        status = data.get('status', self.instance.status if self.instance else 'pending')

        if activity:
            request = self.context.get('request')
            if not request or activity.user_id != request.user.pk:
                raise serializers.ValidationError({
                    'activity': 'Solo puedes asociar subtareas a tus propios eventos.'
                })

            if not scheduled_date:
                raise serializers.ValidationError({
                    "scheduled_date": "Este campo es obligatorio."
                })

            if scheduled_date > activity.date_event:
                raise serializers.ValidationError({
                    "scheduled_date": "La fecha programada no puede ser posterior a la fecha del evento."
                })

            # 2. Horas estimadas positivas
            if estimated_hours is not None and estimated_hours <= 0:
                raise serializers.ValidationError({
                    "estimated_hours": "Las horas estimadas deben ser mayores a 0."
                })

            # 3. Sobrecarga diaria (HU-07): se cuenta el día programado.
            self._check_daily_overload(
                activity.user,
                scheduled_date,
                estimated_hours,
                status,
            )

            data['scheduled_date'] = scheduled_date
            data['due_date'] = activity.date_event

        return data

    def _check_daily_overload(self, user, scheduled_date, estimated_hours, status):
        """Bloquea el guardado si el cambio deja el día por encima del límite.

        Solo se evalúa cuando el cambio AGREGA carga al día. Editar otros campos,
        reducir horas, marcar como hecha o posponer no suman carga, así que un
        día ya sobrecargado (p. ej. tras bajar el límite) se puede seguir
        resolviendo sin quedar bloqueado.
        """
        if (
            not scheduled_date
            or not estimated_hours
            or status not in PLANNED_STATUSES
        ):
            return

        previous = self.instance
        if (
            previous is not None
            and previous.status in PLANNED_STATUSES
            and previous.scheduled_date == scheduled_date
            and estimated_hours <= previous.estimated_hours
        ):
            return

        info = check_day_capacity(
            user,
            scheduled_date,
            estimated_hours,
            exclude_id=previous.id if previous else None,
        )
        if info['exceeds']:
            raise OverloadConflict(info)


class ActivitySerializer(serializers.ModelSerializer):
    subtasks = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    completed_subtasks = serializers.SerializerMethodField()
    total_subtasks = serializers.SerializerMethodField()

    def validate(self, data):
        if self.instance is not None and 'date_event' in data:
            latest_dates = self.instance.subtasks.aggregate(
                latest_due_date=Max('scheduled_date'),
            )
            latest_due_date = latest_dates['latest_due_date']
            if latest_due_date and data['date_event'] < latest_due_date:
                raise serializers.ValidationError({
                    'date_event': (
                        'La fecha del evento no puede ser anterior a la fecha '
                        'programada de una de sus gestiones.'
                    )
                })
        return data

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


_DAILY_LIMIT_RANGE_MESSAGE = (
    f'El límite diario debe ser un número entero de horas entre '
    f'{MIN_DAILY_HOURS_LIMIT} y {MAX_DAILY_HOURS_LIMIT}.'
)


class DailyLimitSerializer(serializers.Serializer):
    """HU-12: valida el límite diario de horas (entero, 1 a 16 incluidos)."""
    daily_hours_limit = serializers.IntegerField(
        min_value=MIN_DAILY_HOURS_LIMIT,
        max_value=MAX_DAILY_HOURS_LIMIT,
        error_messages={
            'required': _DAILY_LIMIT_RANGE_MESSAGE,
            'null': _DAILY_LIMIT_RANGE_MESSAGE,
            'invalid': _DAILY_LIMIT_RANGE_MESSAGE,
            'min_value': _DAILY_LIMIT_RANGE_MESSAGE,
            'max_value': _DAILY_LIMIT_RANGE_MESSAGE,
            'max_string_length': _DAILY_LIMIT_RANGE_MESSAGE,
        },
    )


class DailyLimitPreviewSerializer(DailyLimitSerializer):
    today = serializers.DateField()


class DailyLimitChangeSerializer(serializers.Serializer):
    subtask_id = serializers.IntegerField(min_value=1)
    action = serializers.ChoiceField(choices=('move', 'reduce_hours'))
    scheduled_date = serializers.DateField(required=False)
    due_date = serializers.DateField(required=False)
    estimated_hours = serializers.DecimalField(
        max_digits=4,
        decimal_places=1,
        min_value=Decimal('0.5'),
        required=False,
    )

    def validate(self, attrs):
        if 'due_date' in attrs and 'scheduled_date' not in attrs:
            attrs['scheduled_date'] = attrs.pop('due_date')
        else:
            attrs.pop('due_date', None)
        if attrs['action'] == 'move':
            if 'scheduled_date' not in attrs or 'estimated_hours' in attrs:
                raise serializers.ValidationError(
                    'Mover requiere scheduled_date y no admite estimated_hours.'
                )
        elif 'estimated_hours' not in attrs or 'scheduled_date' in attrs:
            raise serializers.ValidationError(
                'Reducir horas requiere estimated_hours y no admite scheduled_date.'
            )
        elif attrs['estimated_hours'] % Decimal('0.5') != 0:
            raise serializers.ValidationError(
                {'estimated_hours': 'Las horas deben avanzar en incrementos de 0.5.'}
            )
        return attrs


class DailyLimitApplySerializer(DailyLimitPreviewSerializer):
    changes = DailyLimitChangeSerializer(many=True, required=False, default=list)

    def validate_changes(self, changes):
        subtask_ids = [change['subtask_id'] for change in changes]
        if len(subtask_ids) != len(set(subtask_ids)):
            raise serializers.ValidationError(
                'Cada subtarea solo puede aparecer una vez.'
            )
        return changes


class DailyCapacitySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyCapacity
        fields = '__all__'
        read_only_fields = ('user',)


class ResolutionMoveSerializer(serializers.Serializer):
    """HU-08: valida la nueva fecha para mover una gestión."""
    scheduled_date = serializers.DateField(required=False)
    due_date = serializers.DateField(required=False)

    def validate(self, attrs):
        scheduled_date = attrs.get('scheduled_date', attrs.get('due_date'))
        if scheduled_date is None:
            raise serializers.ValidationError({
                'scheduled_date': 'Este campo es obligatorio.'
            })
        attrs['scheduled_date'] = scheduled_date
        attrs.pop('due_date', None)
        return attrs


class ResolutionReduceHoursSerializer(serializers.Serializer):
    """HU-08: valida una nueva estimación positiva de horas.

    El modelo usa una precisión de una décima, por lo que 0.1 es el menor
    valor positivo representable. No existe otro rango mínimo en la HU.
    """
    estimated_hours = serializers.DecimalField(
        max_digits=4,
        decimal_places=1,
        min_value=Decimal('0.1'),
    )


class ExecutionRegistrationSerializer(serializers.Serializer):
    """US-09: valida el estado final y los datos de una tarea ejecutada o pospuesta."""
    status = serializers.ChoiceField(choices=('done', 'postponed'))
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    scheduled_date = serializers.DateField(required=False)
    due_date = serializers.DateField(required=False)

    def validate(self, attrs):
        if attrs.get('status') == 'done' and (
            'due_date' in attrs or 'scheduled_date' in attrs
        ):
            raise serializers.ValidationError({
                'scheduled_date': 'Solo se puede indicar una nueva fecha al posponer la tarea.'
            })
        if 'due_date' in attrs and 'scheduled_date' not in attrs:
            attrs['scheduled_date'] = attrs.pop('due_date')
        else:
            attrs.pop('due_date', None)
        return attrs
