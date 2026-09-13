from rest_framework.routers import DefaultRouter
from .views import JournalActiviteViewSet

router = DefaultRouter()
router.register('logs', JournalActiviteViewSet, basename='journal')

urlpatterns = router.urls