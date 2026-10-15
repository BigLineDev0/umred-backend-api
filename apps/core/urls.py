from rest_framework.routers import DefaultRouter, path
from .views import (
    JournalActiviteViewSet, mon_activite, rapports_export_excel, rapports_export_pdf, indicateurs_pilotage,
    ContactView, tache_cloturer_reservations, tache_envoyer_rappels, tache_synthese_hebdomadaire, tache_synthese_rapport,
)

router = DefaultRouter()
router.register('logs', JournalActiviteViewSet, basename='journal')

urlpatterns = router.urls

from .views import recherche_globale
urlpatterns += [
    path('contact/', ContactView.as_view(), name='contact'),
    path('recherche/', recherche_globale, name='recherche-globale'),
    path('mon-activite/', mon_activite, name='mon-activite'),
    path('rapports/export/', rapports_export_excel, name='rapports-export-excel'),
    path('rapports/export-pdf/', rapports_export_pdf, name='rapports-export-pdf'),
    path('pilotage/indicateurs/', indicateurs_pilotage, name='indicateurs-pilotage'),
    path('taches/cloturer-reservations/', tache_cloturer_reservations, name='tache-cloturer-reservations'),
    path('taches/envoyer-rappels/', tache_envoyer_rappels, name='tache-envoyer-rappels'),
    path('taches/synthese-hebdomadaire/', tache_synthese_hebdomadaire, name='tache-synthese-hebdomadaire'),
    path('taches/synthese-hebdomadaire/rapport/', tache_synthese_rapport, name='tache-synthese-rapport'),
]
