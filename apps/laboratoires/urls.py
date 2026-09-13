from rest_framework.routers import DefaultRouter
from .views import LaboratoireViewSet

router = DefaultRouter()
router.register('', LaboratoireViewSet, basename='laboratoire')

urlpatterns = router.urls