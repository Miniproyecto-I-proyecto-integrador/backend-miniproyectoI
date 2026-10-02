from django.db.models import Prefetch, Sum
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Activity, Subtask, DailyCapacity
from .serializers import ActivitySerializer, SubtaskSerializer, DailyCapacitySerializer

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
        return Response({
            'date': date,
            'assigned_hours': float(assigned),
            'limit_hours': 6,
            'overloaded': float(assigned) > 6,
        })

