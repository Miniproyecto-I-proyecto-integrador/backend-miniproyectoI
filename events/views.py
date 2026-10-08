from django.db.models import Prefetch, Sum
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import (
    Activity, Subtask, DailyCapacity, OrganizerSettings,
    DEFAULT_DAILY_HOURS_LIMIT, MIN_DAILY_HOURS_LIMIT, MAX_DAILY_HOURS_LIMIT,
)
from .serializers import (
    ActivitySerializer, SubtaskSerializer, DailyCapacitySerializer, DailyLimitSerializer,
)
from .services import get_daily_limit, set_daily_limit

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
        date = request.query_params.get('date')
        filters = {'user': request.user, 'activity__user': request.user}
        if date:
            filters['scheduled_date'] = date
        assigned = Subtask.objects.filter(**filters).exclude(status='done').aggregate(
            total=Sum('estimated_hours')
        )['total'] or 0
        limit = get_daily_limit(request.user)
        return Response({
            'date': date,
            'assigned_hours': float(assigned),
            'limit_hours': limit,
            'overloaded': float(assigned) > limit,
        })

### Vista para el límite diario de horas de gestión del organizador autenticado (HU-12)
class DailyLimitView(APIView):
    """HU-12: límite diario de horas de gestión del organizador autenticado.

    GET devuelve el límite actual (6 h por defecto si nunca lo configuró).
    PUT/PATCH lo actualiza; solo afecta al usuario del token.
    """

    def _payload(self, user):
        saved = OrganizerSettings.objects.filter(user=user).first()
        return {
            'daily_hours_limit': saved.daily_hours_limit if saved else DEFAULT_DAILY_HOURS_LIMIT,
            'is_default': saved is None,
            'min_hours': MIN_DAILY_HOURS_LIMIT,
            'max_hours': MAX_DAILY_HOURS_LIMIT,
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

