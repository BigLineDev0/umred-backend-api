from rest_framework.routers import DefaultRouter, path
from .views import JournalActiviteViewSet, mon_activite, rapports_export_excel

router = DefaultRouter()
router.register('logs', JournalActiviteViewSet, basename='journal')

urlpatterns = router.urls

from .views import recherche_globale
urlpatterns += [
    path('recherche/', recherche_globale, name='recherche-globale'),
    path('mon-activite/', mon_activite, name='mon-activite'),
    path('rapports/export/', rapports_export_excel, name='rapports-export-excel'),
]
