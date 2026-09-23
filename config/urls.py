from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenRefreshView
from apps.utilisateurs.views import ChangerMotDePasseView, LogoutView, MonProfilView

from apps.utilisateurs.views import RegisterView, UmredTokenObtainPairView, VerifierJetonView, DefinirMotDePasseView


from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),

    path('api/auth/login/', UmredTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/logout/', LogoutView.as_view(), name='logout'),
    
    path('api/auth/verifier-jeton/<str:jeton>/', VerifierJetonView.as_view(), name='verifier-jeton'),
    path('api/auth/definir-mot-de-passe/', DefinirMotDePasseView.as_view(), name='definir-mot-de-passe'),
    path('api/auth/changer-mot-de-passe/', ChangerMotDePasseView.as_view(), name='changer-mot-de-passe'),
    
    path('api/utilisateurs/moi/', MonProfilView.as_view(), name='mon-profil'),
    
    path('api/reservations/', include('apps.reservations.urls')),
    path('api/maintenances/', include('apps.maintenances.urls')),
    path('api/laboratoires/', include('apps.laboratoires.urls')),
    path('api/equipements/', include('apps.equipements.urls')),
    path('api/', include('apps.core.urls')),
    path('api/notifications/', include('apps.notifications.urls')),
    path('api/utilisateurs/', include('apps.utilisateurs.urls')),
    
    path('api/consommables/', include('apps.consommables.urls')),
    path('api/projets/', include('apps.projets.urls')),
    
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

