from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from rest_framework.views import APIView
from .models import (
    Activity, Subtask, DailyCapacity, OrganizerSettings,
    DEFAULT_DAILY_HOURS_LIMIT, MIN_DAILY_HOURS_LIMIT, MAX_DAILY_HOURS_LIMIT,
)
from .serializers import (
    ActivitySerializer, SubtaskSerializer, DailyCapacitySerializer, DailyLimitSerializer,
    ResolutionMoveSerializer, ResolutionReduceHoursSerializer,
)
from .services import (
    RESOLUTION_OPTIONS, OverloadConflict, find_overloaded_days,
    get_daily_limit, get_day_load, set_daily_limit, check_day_capacity,
    suggest_available_day,
)

#Se usará em ModelViewSet para poder validar que se entrega todo, sin embargo en los siguientes sprints se endurecerá esta medida
#Es sólo para el MVP
class ActivityViewSet(viewsets.ModelViewSet):
    serializer_class = ActivitySerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Activity.objects.none()
        # Cada consulta parte del propietario autenticado, también en acciones de detalle.
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

class SubtaskViewSet(viewsets.ModelViewSet):
    serializer_class = SubtaskSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Subtask.objects.none()
        return Subtask.objects.filter(user=self.request.user).select_related('activity')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def handle_exception(self, exc):
        # HU-07: un cambio que sobrecarga el día no se guarda y se responde con
        # el mensaje y las cifras del conflicto (horas actuales, propuestas, total, límite).
        if isinstance(exc, OverloadConflict):
            return Response(exc.response_data(), status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)

    @action(detail=True, methods=['get'], url_path='sugerir-dia')
    def sugerir_dia(self, request, pk=None):
        """HU-08: devuelve el primer día posterior con capacidad disponible."""
        subtask = self.get_object()
        if subtask.status not in ('pending', 'in_progress'):
            return Response(
                {'detail': 'Solo se puede resolver un conflicto de una gestión activa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        suggestion = suggest_available_day(request.user, subtask)
        if suggestion is None:
            return Response({
                'suggestion': None,
                'message': 'No hay un día disponible antes de la fecha del evento.',
            })

        return Response({
            'suggestion': suggestion,
            'conflict_options': list(RESOLUTION_OPTIONS),
        })

    @action(detail=True, methods=['patch'], url_path='resolver-mover')
    def resolver_mover(self, request, pk=None):
        """HU-08: mueve una gestión si el nuevo día tiene capacidad."""
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

        with transaction.atomic():
            serializer = SubtaskSerializer(
                subtask,
                data={'due_date': new_date},
                partial=True,
                context={'request': request},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

        return Response({
            'resolution': 'move',
            'conflict_resolved': True,
            'subtask': SubtaskSerializer(subtask, context={'request': request}).data,
        })

    @action(detail=True, methods=['patch'], url_path='resolver-reducir')
    def resolver_reducir(self, request, pk=None):
        """HU-08: reduce horas y devuelve el conflicto actualizado si persiste."""
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


class DailyCapacityViewSet(viewsets.ModelViewSet):
    serializer_class = DailyCapacitySerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return DailyCapacity.objects.none()
        return DailyCapacity.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'], url_path='resumen')
    def resumen(self, request):
        """Carga de un día (due_date): horas asignadas, límite y si hay sobrecarga.

        ``?date=AAAA-MM-DD`` (opcional; por defecto hoy en hora de Bogotá).
        Solo cuentan gestiones pendientes o en curso del usuario autenticado.
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
            day = timezone.localdate()

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
    """HU-12: límite diario de horas de gestión del organizador autenticado.

    GET devuelve el límite actual (6 h por defecto si nunca lo configuró).
    PUT/PATCH lo actualiza; solo afecta al usuario del token.
    """

    def _payload(self, user):
        saved = OrganizerSettings.objects.filter(user=user).first()
        limit = saved.daily_hours_limit if saved else DEFAULT_DAILY_HOURS_LIMIT
        # Días (desde hoy) que superan el límite actual. Al bajar el límite el
        # cambio se guarda igual y se avisa, para resolver con mover / reducir / posponer.
        overloaded_days = find_overloaded_days(user, limit=limit)
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
        return Response(self._payload(request.user))

    def _update(self, request):
        serializer = DailyLimitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_daily_limit(request.user, serializer.validated_data['daily_hours_limit'])
        return Response(self._payload(request.user))

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

