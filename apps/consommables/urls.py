from rest_framework.routers import DefaultRouter
from .views import ConsommableViewSet

router = DefaultRouter()
router.register('', ConsommableViewSet, basename='consommable')
urlpatterns = router.urls