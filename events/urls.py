from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    ActivityViewSet,
    SubtaskViewSet,
    DailyCapacityViewSet,
    DailyLimitView,
    DailyLimitConflictPreviewView,
    DailyLimitConflictApplyView,
)

router = DefaultRouter()
router.register(r'actividades', ActivityViewSet, basename='activity')
router.register(r'subtareas', SubtaskViewSet, basename='subtask')
router.register(r'capacidad-diaria', DailyCapacityViewSet, basename='daily-capacity')

urlpatterns = [
    path('configuracion/limite-diario/', DailyLimitView.as_view(), name='daily-limit'),
    path(
        'configuracion/limite-diario/previsualizar/',
        DailyLimitConflictPreviewView.as_view(),
        name='daily-limit-preview',
    ),
    path(
        'configuracion/limite-diario/aplicar/',
        DailyLimitConflictApplyView.as_view(),
        name='daily-limit-apply',
    ),
] + router.urls
