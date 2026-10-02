from django.utils import timezone
from django.db.models import Sum
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from .models import Activity, Subtask, DailyCapacity
from .serializers import (
    ActivitySerializer,
    TodayActivitiesResponseSerializer,
    DailyCapacitySerializer,
    SubtaskSerializer,
    TodaySubtaskSerializer,
)

TASK_STATUS_ALIASES = {
    'pending': 'pending',
    'pendiente': 'pending',
    'in_progress': 'in_progress',
    'en_progreso': 'in_progress',
    'en curso': 'in_progress',
    'done': 'done',
    'hecho': 'done',
    'hecha': 'done',
    'completado': 'done',
    'completada': 'done',
    'postponed': 'postponed',
    'pospuesto': 'postponed',
    'pospuesta': 'postponed',
}

#Se usará em ModelViewSet para poder validar que se entrega todo, sin embargo en los siguientes sprints se endurecerá esta medida
#Es sólo para el MVP
class ActivityViewSet(viewsets.ModelViewSet):
    queryset = Activity.objects.prefetch_related('subtasks').all()
    serializer_class = ActivitySerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Activity.objects.none()
        return Activity.objects.filter(user_id=self.request.user.pk).prefetch_related('subtasks')

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user.pk)

    @swagger_auto_schema(
        method='get',
        operation_summary='Gestiones agrupadas para la vista Hoy',
        operation_description=(
            'Filtra por nombre del evento con `curso` y por estado de la gestión con `estado`. '
            'Devuelve vencidas, para hoy y próximas; ordena por fecha límite, horas estimadas e ID.'
        ),
        manual_parameters=[
            openapi.Parameter(
                'curso', openapi.IN_QUERY, description='Nombre (o parte del nombre) del evento.',
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                'estado', openapi.IN_QUERY,
                description='Estado: pendiente, en_progreso, completada o pospuesta.',
                type=openapi.TYPE_STRING,
            ),
        ],
        responses={200: TodayActivitiesResponseSerializer},
    )
    @action(detail=False, methods=['get'], url_path='hoy')
    def hoy(self, request):
        today = timezone.localdate()
        curso = request.query_params.get('curso', '').strip()
        estado = request.query_params.get('estado', '').strip().casefold()

        subtasks = Subtask.objects.filter(activity__user_id=request.user.pk)
        if curso:
            subtasks = subtasks.filter(activity__name__icontains=curso)
        if estado:
            normalized_status = TASK_STATUS_ALIASES.get(estado)
            if normalized_status is None:
                raise ValidationError({
                    'estado': 'Estado inválido. Usa pendiente, en_progreso, completada o pospuesta.'
                })
            subtasks = subtasks.filter(status=normalized_status)

        ordered_subtasks = list(
            subtasks.select_related('activity').order_by('due_date', 'estimated_hours', 'id')
        )
        groups = {'vencidas': [], 'para_hoy': [], 'proximas': []}
        for subtask in ordered_subtasks:
            if subtask.due_date < today:
                groups['vencidas'].append(subtask)
            elif subtask.due_date == today:
                groups['para_hoy'].append(subtask)
            else:
                groups['proximas'].append(subtask)

        serialized_groups = {
            key: TodaySubtaskSerializer(group, many=True).data
            for key, group in groups.items()
        }
        return Response({
            'fecha': today,
            'filtros': {'curso': curso or None, 'estado': estado or None},
            'grupos': serialized_groups,
            'total': len(ordered_subtasks),
        })

    @action(detail=True, methods=['get'])
    def progreso(self, request, pk=None):
        activity = self.get_object()
        subtasks = activity.subtasks.all()
        total = subtasks.count()
        completed = subtasks.filter(status='done').count()
        return Response({
            'activity_id': activity.id,
            'progress': round(completed / total * 100) if total else 0,
            'completed_subtasks': completed,
            'total_subtasks': total,
        })

class SubtaskViewSet(viewsets.ModelViewSet):
    queryset = Subtask.objects.all()
    serializer_class = SubtaskSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Subtask.objects.none()
        return Subtask.objects.filter(activity__user_id=self.request.user.pk).select_related('activity')

class DailyCapacityViewSet(viewsets.ModelViewSet):
    queryset = DailyCapacity.objects.all()
    serializer_class = DailyCapacitySerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return DailyCapacity.objects.none()
        return DailyCapacity.objects.filter(user_id=self.request.user.pk)

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user.pk)

    @action(detail=False, methods=['get'], url_path='resumen')
    def resumen(self, request):
        date = request.query_params.get('date')
        filters = {'scheduled_date': date} if date else {}
        filters['activity__user_id'] = request.user.pk
        assigned = Subtask.objects.filter(**filters).exclude(status='done').aggregate(
            total=Sum('estimated_hours')
        )['total'] or 0
        return Response({
            'date': date,
            'assigned_hours': float(assigned),
            'limit_hours': 6,
            'overloaded': float(assigned) > 6,
        })

