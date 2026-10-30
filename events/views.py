from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Activity, Subtask, DailyCapacity, OrganizerSettings,
    DEFAULT_DAILY_HOURS_LIMIT, MIN_DAILY_HOURS_LIMIT, MAX_DAILY_HOURS_LIMIT,
)
from .serializers import (
    ActivitySerializer, SubtaskSerializer, DailyCapacitySerializer,
    DailyLimitSerializer, DailyLimitPreviewSerializer, DailyLimitApplySerializer,
    ResolutionMoveSerializer, ResolutionReduceHoursSerializer,
    ExecutionRegistrationSerializer,
)
from .services import (
    PLANNED_STATUSES, RESOLUTION_OPTIONS, OverloadConflict, find_overloaded_days,
    get_daily_limit, get_day_load, set_daily_limit, check_day_capacity,
    suggest_available_day,
)


def _query_date(request, name, fallback=None):
    """Lee una fecha YYYY-MM-DD enviada por el cliente o usa fallback."""
    raw = request.query_params.get(name)
    if not raw:
        return fallback
    try:
        parsed = parse_date(raw)
    except ValueError:
        # Formato correcto pero fecha inexistente (p. ej. 2026-02-30).
        parsed = None
    if parsed is None:
        raise ValueError(name)
    return parsed


class OrganizerMutationLockMixin:
    def _lock_organizer(self):
        user = self.request.user
        user.__class__.objects.select_for_update().only('pk').get(pk=user.pk)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        self._lock_organizer()
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self._lock_organizer()
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self._lock_organizer()
        return super().destroy(request, *args, **kwargs)


class ActivityViewSet(OrganizerMutationLockMixin, viewsets.ModelViewSet):
    serializer_class = ActivitySerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Activity.objects.none()
        owned_subtasks = Subtask.objects.filter(user=self.request.user)
        return Activity.objects.filter(user=self.request.user).prefetch_related(
            Prefetch('subtasks', queryset=owned_subtasks, to_attr='_owned_subtasks')
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        activity = serializer.save()
        Subtask.objects.filter(
            activity=activity,
            user=self.request.user,
        ).update(due_date=activity.date_event)

    @swagger_auto_schema(operation_summary='Consultar el progreso de preparación del evento')
    @action(detail=True, methods=['get'])
    def progreso(self, request, pk=None):
        activity = self.get_object()
        subtasks = Subtask.objects.filter(activity=activity, user=request.user)
        total = subtasks.count()
        completed = subtasks.filter(status='done').count()
        return Response({
            'activity_id': activity.id,
            'progress': round(completed / total * 100) if total else 0,
            'completed_subtasks': completed,
            'total_subtasks': total,
        })


class SubtaskViewSet(OrganizerMutationLockMixin, viewsets.ModelViewSet):
    serializer_class = SubtaskSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Subtask.objects.none()
        return Subtask.objects.filter(user=self.request.user).select_related('activity')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @swagger_auto_schema(
        request_body=ExecutionRegistrationSerializer,
        operation_summary='Registrar una tarea como hecha o pospuesta',
    )
    @action(detail=True, methods=['patch'], url_path='registrar-ejecucion')
    @transaction.atomic
    def registrar_ejecucion(self, request, pk=None):
        self._lock_organizer()
        subtask = self.get_object()
        input_serializer = ExecutionRegistrationSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        serializer = SubtaskSerializer(
            subtask,
            data=input_serializer.validated_data,
            partial=True,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def handle_exception(self, exc):
        if isinstance(exc, OverloadConflict):
            return Response(exc.response_data(), status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)

    @swagger_auto_schema(
        operation_summary='Sugerir día disponible para una gestión',
    )
    @action(detail=True, methods=['get'], url_path='sugerir-dia')
    def sugerir_dia(self, request, pk=None):
        subtask = self.get_object()
        if subtask.status not in PLANNED_STATUSES:
            return Response(
                {'detail': 'Solo se puede resolver un conflicto de una gestión activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            client_today = _query_date(request, 'today', timezone.localdate())
            start_date = _query_date(request, 'desde', None)
        except ValueError as exc:
            return Response(
                {str(exc): ['Fecha inválida. Usa el formato AAAA-MM-DD.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        suggestion = suggest_available_day(
            request.user,
            subtask,
            start_date=start_date or client_today,
        )
        if suggestion is None:
            return Response({
                'suggestion': None,
                'message': 'No hay un día disponible antes de la fecha del evento.',
            })

        return Response({
            'suggestion': suggestion,
            'conflict_options': list(RESOLUTION_OPTIONS),
        })

    @swagger_auto_schema(
        request_body=ResolutionMoveSerializer,
        operation_summary='Resolver conflicto moviendo una gestión',
    )
    @action(detail=True, methods=['patch'], url_path='resolver-mover')
    @transaction.atomic
    def resolver_mover(self, request, pk=None):
        self._lock_organizer()
        subtask = self.get_object()
        if subtask.status not in PLANNED_STATUSES:
            return Response(
                {'detail': 'Solo se puede resolver un conflicto de una gestión activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        input_serializer = ResolutionMoveSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        new_date = input_serializer.validated_data['scheduled_date']

        if new_date == subtask.scheduled_date:
            return Response(
                {'scheduled_date': ['La nueva fecha debe ser diferente a la fecha actual.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        origin_date = subtask.scheduled_date

        with transaction.atomic():
            serializer = SubtaskSerializer(
                subtask,
                data={'scheduled_date': new_date},
                partial=True,
                context={'request': request},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

        origin_info = check_day_capacity(request.user, origin_date, Decimal('0'))
        persists = origin_info['exceeds']
        response = {
            'resolution': 'move',
            'conflict_resolved': not persists,
            'conflict_persists': persists,
            'subtask': SubtaskSerializer(subtask, context={'request': request}).data,
        }
        if persists:
            response['conflict'] = {
                **origin_info,
                'options': list(RESOLUTION_OPTIONS),
            }
        return Response(response)

    @swagger_auto_schema(
        request_body=ResolutionReduceHoursSerializer,
        operation_summary='Resolver conflicto reduciendo horas estimadas',
    )
    @action(detail=True, methods=['patch'], url_path='resolver-reducir')
    @transaction.atomic
    def resolver_reducir(self, request, pk=None):
        self._lock_organizer()
        subtask = self.get_object()
        if subtask.status not in PLANNED_STATUSES:
            return Response(
                {'detail': 'Solo se puede resolver un conflicto de una gestión activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        input_serializer = ResolutionReduceHoursSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        new_hours = input_serializer.validated_data['estimated_hours']

        if new_hours >= subtask.estimated_hours:
            return Response(
                {'estimated_hours': ['Las horas nuevas deben ser menores que las horas actuales.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            serializer = SubtaskSerializer(
                subtask,
                data={'estimated_hours': new_hours},
                partial=True,
                context={'request': request},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

        info = check_day_capacity(
            request.user,
            subtask.scheduled_date,
            subtask.estimated_hours,
            exclude_id=subtask.id,
        )

        response = {
            'resolution': 'reduce_hours',
            'conflict_resolved': not info['exceeds'],
            'conflict_persists': info['exceeds'],
            'subtask': SubtaskSerializer(subtask, context={'request': request}).data,
        }
        if info['exceeds']:
            response['conflict'] = {**info, 'options': list(RESOLUTION_OPTIONS)}
        return Response(response)


class DailyCapacityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DailyCapacitySerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return DailyCapacity.objects.none()
        return DailyCapacity.objects.filter(user=self.request.user)

    @action(detail=False, methods=['get'], url_path='resumen')
    def resumen(self, request):
        """Carga de un día usando scheduled_date y solo gestiones planificadas.

        ``?date=AAAA-MM-DD`` permite consultar un día concreto. Si no se
        envía, se usa ``?today=AAAA-MM-DD`` del cliente y, como respaldo,
        la fecha local del servidor.
        """
        raw_date = request.query_params.get('date')
        if raw_date:
            try:
                day = parse_date(raw_date)
            except ValueError:
                day = None
            if day is None:
                return Response(
                    {'date': ['Fecha inválida. Usa el formato AAAA-MM-DD.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            try:
                day = _query_date(request, 'today', timezone.localdate())
            except ValueError:
                return Response(
                    {'today': ['Fecha inválida. Usa el formato AAAA-MM-DD.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        limit = get_daily_limit(request.user)
        assigned = get_day_load(request.user, day)
        return Response({
            'date': day.isoformat(),
            'assigned_hours': float(assigned),
            'limit_hours': limit,
            'remaining_hours': float(max(limit - assigned, 0)),
            'overloaded': assigned > limit,
        })


class DailyLimitView(APIView):
    def _payload(self, user, from_date=None):
        saved = OrganizerSettings.objects.filter(user=user).first()
        limit = saved.daily_hours_limit if saved else DEFAULT_DAILY_HOURS_LIMIT
        overloaded_days = find_overloaded_days(
            user,
            limit=limit,
            from_date=from_date,
        )
        return {
            'daily_hours_limit': limit,
            'is_default': saved is None,
            'min_hours': MIN_DAILY_HOURS_LIMIT,
            'max_hours': MAX_DAILY_HOURS_LIMIT,
            'has_conflicts': bool(overloaded_days),
            'overloaded_days': overloaded_days,
            'resolution_options': list(RESOLUTION_OPTIONS),
        }

    @swagger_auto_schema(operation_summary='Ver límite diario de horas de gestión')
    def get(self, request):
        try:
            today = _query_date(request, 'today', timezone.localdate())
        except ValueError:
            return Response(
                {'today': ['Fecha inválida. Usa el formato AAAA-MM-DD.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self._payload(request.user, from_date=today))

    @transaction.atomic
    def _update(self, request):
        request.user.__class__.objects.select_for_update().only('pk').get(
            pk=request.user.pk,
        )
        serializer = DailyLimitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            today = _query_date(request, 'today', timezone.localdate())
        except ValueError:
            return Response(
                {'today': ['Fecha inválida. Usa el formato AAAA-MM-DD.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        set_daily_limit(request.user, serializer.validated_data['daily_hours_limit'])
        return Response(self._payload(request.user, from_date=today))

    @swagger_auto_schema(
        request_body=DailyLimitSerializer,
        operation_summary='Actualizar límite diario de horas de gestión (1 a 16)',
    )
    def put(self, request):
        return self._update(request)

    @swagger_auto_schema(
        request_body=DailyLimitSerializer,
        operation_summary='Actualizar límite diario de horas de gestión (1 a 16)',
    )
    def patch(self, request):
        return self._update(request)


def _planned_loads_and_subtasks(user, from_date):
    subtasks = list(
        Subtask.objects.filter(
            user=user,
            activity__user=user,
            status__in=PLANNED_STATUSES,
            scheduled_date__gte=from_date,
        )
        .select_related('activity')
        .order_by('scheduled_date', 'id')
    )
    loads = {}
    for subtask in subtasks:
        loads[subtask.scheduled_date] = (
            loads.get(subtask.scheduled_date, Decimal('0')) + subtask.estimated_hours
        )
    return subtasks, loads


def _overloaded_days_from_plan(subtasks, loads, limit, projected=None):
    tasks_by_day = {}
    for subtask in subtasks:
        change = projected.get(subtask.id) if projected else None
        scheduled_date = (
            change['scheduled_date'] if change else subtask.scheduled_date
        )
        estimated_hours = (
            change['estimated_hours'] if change else subtask.estimated_hours
        )
        tasks_by_day.setdefault(scheduled_date, []).append({
            'id': subtask.id,
            'name': subtask.name,
            'activity_id': subtask.activity_id,
            'activity_name': subtask.activity.name,
            'estimated_hours': float(estimated_hours),
            'status': subtask.status,
        })
    return [
        {
            'date': day.isoformat(),
            'total_hours': float(total),
            'limit_hours': limit,
            'excess_hours': float(total - limit),
            'subtasks': tasks_by_day[day],
        }
        for day, total in sorted(loads.items())
        if total > limit
    ]


class DailyLimitConflictPreviewView(APIView):
    @swagger_auto_schema(
        request_body=DailyLimitPreviewSerializer,
        operation_summary='Previsualizar conflictos antes de cambiar el límite diario',
    )
    def post(self, request):
        serializer = DailyLimitPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        limit = serializer.validated_data['daily_hours_limit']
        today = serializer.validated_data['today']
        subtasks, loads = _planned_loads_and_subtasks(request.user, today)
        overloaded_days = _overloaded_days_from_plan(subtasks, loads, limit)
        tasks_by_id = {subtask.id: subtask for subtask in subtasks}

        for day in overloaded_days:
            for task_data in day['subtasks']:
                subtask = tasks_by_id[task_data['id']]
                candidates = []
                candidate_day = today + timedelta(days=1)
                while candidate_day <= subtask.activity.date_event and len(candidates) < 3:
                    current = loads.get(candidate_day, Decimal('0'))
                    if subtask.scheduled_date == candidate_day:
                        current -= subtask.estimated_hours
                    projected = current + subtask.estimated_hours
                    if projected <= limit:
                        candidates.append({
                            'date': candidate_day.isoformat(),
                            'current_hours': float(current),
                            'total_hours': float(projected),
                            'limit_hours': limit,
                        })
                    candidate_day += timedelta(days=1)
                task_data['suggested_dates'] = candidates

        return Response({
            'daily_hours_limit': limit,
            'has_conflicts': bool(overloaded_days),
            'overloaded_days': overloaded_days,
            'daily_loads': [
                {'date': day.isoformat(), 'total_hours': float(total)}
                for day, total in sorted(loads.items())
            ],
        })


class DailyLimitConflictApplyView(APIView):
    @transaction.atomic
    @swagger_auto_schema(
        request_body=DailyLimitApplySerializer,
        operation_summary='Guardar límite diario y resolver conflictos en una transacción',
    )
    def post(self, request):
        serializer = DailyLimitApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        limit = values['daily_hours_limit']
        today = values['today']

        request.user.__class__.objects.select_for_update().only('pk').get(
            pk=request.user.pk,
        )

        subtasks, loads = _planned_loads_and_subtasks(request.user, today)
        tasks_by_id = {subtask.id: subtask for subtask in subtasks}
        changes = values['changes']
        projected = {
            subtask.id: {
                'scheduled_date': subtask.scheduled_date,
                'estimated_hours': subtask.estimated_hours,
            }
            for subtask in subtasks
        }

        for change in changes:
            subtask = tasks_by_id.get(change['subtask_id'])
            if subtask is None:
                return Response(
                    {'detail': 'Una de las gestiones ya no está activa o no te pertenece.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if change['action'] == 'move':
                new_date = change['scheduled_date']
                if new_date < today or new_date > subtask.activity.date_event:
                    return Response(
                        {'scheduled_date': 'La fecha debe estar entre hoy y la fecha del evento.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if new_date == subtask.scheduled_date:
                    return Response(
                        {'scheduled_date': 'La nueva fecha debe ser diferente a la actual.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                projected[subtask.id]['scheduled_date'] = new_date
            else:
                new_hours = change['estimated_hours']
                if (
                    new_hours >= subtask.estimated_hours
                    or new_hours % Decimal('0.5') != 0
                ):
                    return Response(
                        {
                            'estimated_hours': (
                                'Las horas deben reducirse en incrementos de 0.5 '
                                'y no pueden ser menores que las actuales.'
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                projected[subtask.id]['estimated_hours'] = new_hours

        projected_loads = {}
        for subtask in subtasks:
            change = projected[subtask.id]
            projected_loads[change['scheduled_date']] = (
                projected_loads.get(change['scheduled_date'], Decimal('0'))
                + change['estimated_hours']
            )

        overloaded_days = _overloaded_days_from_plan(
            subtasks,
            projected_loads,
            limit,
            projected=projected,
        )
        if overloaded_days:
            return Response({
                'detail': 'Aún hay días que superan el nuevo límite.',
                'daily_hours_limit': limit,
                'has_conflicts': True,
                'overloaded_days': overloaded_days,
            }, status=status.HTTP_400_BAD_REQUEST)

        applied_changes = []
        for change in changes:
            subtask = tasks_by_id[change['subtask_id']]
            updated = projected[subtask.id]
            update_fields = []
            if subtask.scheduled_date != updated['scheduled_date']:
                subtask.scheduled_date = updated['scheduled_date']
                update_fields.append('scheduled_date')
            if subtask.estimated_hours != updated['estimated_hours']:
                subtask.estimated_hours = updated['estimated_hours']
                update_fields.append('estimated_hours')
            if update_fields:
                subtask.save(update_fields=update_fields)
            applied_changes.append({
                'subtask_id': subtask.id,
                'action': change['action'],
                'due_date': subtask.due_date.isoformat(),
                'scheduled_date': subtask.scheduled_date.isoformat(),
                'estimated_hours': float(subtask.estimated_hours),
            })

        set_daily_limit(request.user, limit)
        return Response({
            'daily_hours_limit': limit,
            'has_conflicts': False,
            'overloaded_days': [],
            'changes': applied_changes,
        })
