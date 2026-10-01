from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import OrganisationsPubliquesView, OrganisationViewSet

router = DefaultRouter()
router.register('', OrganisationViewSet, basename='organisation')

# Avant le routeur : sinon « publiques » serait pris pour un identifiant.
urlpatterns = [
    path('publiques/', OrganisationsPubliquesView.as_view(), name='organisations-publiques'),
] + router.urls
