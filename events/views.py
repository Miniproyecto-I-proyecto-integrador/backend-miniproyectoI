from django.shortcuts import render
from django.db.models import Sum
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Activity, Subtask, DailyCapacity
from .serializers import ActivitySerializer, SubtaskSerializer, DailyCapacitySerializer

#Se usará em ModelViewSet para poder validar que se entrega todo, sin embargo en los siguientes sprints se endurecerá esta medida
#Es sólo para el MVP
class ActivityViewSet(viewsets.ModelViewSet):
    queryset = Activity.objects.prefetch_related('subtasks').all()
    serializer_class = ActivitySerializer

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

class DailyCapacityViewSet(viewsets.ModelViewSet):
    queryset = DailyCapacity.objects.all()
    serializer_class = DailyCapacitySerializer

    @action(detail=False, methods=['get'], url_path='resumen')
    def resumen(self, request):
        date = request.query_params.get('date')
        user_id = request.query_params.get('user_id')
        filters = {'scheduled_date': date} if date else {}
        if user_id:
            filters['activity__user_id'] = user_id
        assigned = Subtask.objects.filter(**filters).exclude(status='done').aggregate(
            total=Sum('estimated_hours')
        )['total'] or 0
        return Response({
            'date': date,
            'assigned_hours': float(assigned),
            'limit_hours': 6,
            'overloaded': float(assigned) > 6,
        })

