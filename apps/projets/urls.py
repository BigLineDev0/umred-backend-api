from rest_framework.routers import DefaultRouter
from .views import ProjetViewSet

router = DefaultRouter()
router.register('', ProjetViewSet, basename='projet')
urlpatterns = router.urls