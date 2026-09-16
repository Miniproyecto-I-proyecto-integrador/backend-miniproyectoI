from django.shortcuts import render
from rest_framework import viewsets
from .models import Activity, Subtask, DailyCapacity
from .serializers import ActivitySerializer, SubtaskSerializer, DailyCapacitySerializer

#Se usará em ModelViewSet para poder validar que se entrega todo, sin embargo en los siguientes sprints se endurecerá esta medida
#Es sólo para el MVP
class ActivityViewSet(viewsets.ModelViewSet):
    queryset = Activity.objects.all()
    serializer_class = ActivitySerializer

class SubtaskViewSet(viewsets.ModelViewSet):
    queryset = Subtask.objects.all()
    serializer_class = SubtaskSerializer

class DailyCapacityViewSet(viewsets.ModelViewSet):
    queryset = DailyCapacity.objects.all()
    serializer_class = DailyCapacitySerializer

