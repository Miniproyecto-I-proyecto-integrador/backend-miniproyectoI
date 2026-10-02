from rest_framework.routers import DefaultRouter
from .views import ActivityViewSet, SubtaskViewSet, DailyCapacityViewSet

router = DefaultRouter()
router.register(r'actividades', ActivityViewSet, basename='activity')
router.register(r'subtareas', SubtaskViewSet, basename='subtask')
router.register(r'capacidad-diaria', DailyCapacityViewSet, basename='daily-capacity')

urlpatterns = router.urls