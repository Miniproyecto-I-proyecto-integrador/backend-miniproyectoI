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
    DailyLimitSerializer, ResolutionMoveSerializer, ResolutionReduceHoursSerializer,
)
from .services import (
    RESOLUTION_OPTIONS, OverloadConflict, find_overloaded_days,
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
        if subtask.status not in ('pending', 'in_progress'):
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
        if subtask.status not in ('pending', 'in_progress'):
            return Response(
                {'detail': 'Solo se puede resolver un conflicto de una gestión activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        input_serializer = ResolutionMoveSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        new_date = input_serializer.validated_data['due_date']

        if new_date == subtask.due_date:
            return Response(
                {'due_date': ['La nueva fecha debe ser diferente a la fecha actual.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        origin_date = subtask.due_date

        with transaction.atomic():
            serializer = SubtaskSerializer(
                subtask,
                data={'due_date': new_date},
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
        if subtask.status not in ('pending', 'in_progress'):
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
            subtask.due_date,
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
        """Carga de un día usando due_date y solo gestiones activas.

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
