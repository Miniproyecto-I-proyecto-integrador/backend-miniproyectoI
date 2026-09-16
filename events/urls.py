from rest_framework.routers import DefaultRouter
from .views import ActivityViewSet, SubtaskViewSet, DailyCapacityViewSet

router = DefaultRouter()
router.register(r'actividades', ActivityViewSet)
router.register(r'subtareas', SubtaskViewSet)
router.register(r'capacidad-diaria', DailyCapacityViewSet)

urlpatterns = router.urls