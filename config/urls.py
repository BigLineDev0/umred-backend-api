from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenRefreshView
from apps.utilisateurs.views import ChangerMotDePasseView, LogoutView, MonProfilView

from apps.utilisateurs.views import RegisterView, SenlabTokenObtainPairView, VerifierJetonView, DefinirMotDePasseView, MotDePasseOublieView
from apps.utilisateurs.views import ActiverCompteView, RenvoyerActivationView
from apps.utilisateurs.authentication import TokenRefreshEtablissementSerializer


from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),

    path('api/auth/login/', SenlabTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(serializer_class=TokenRefreshEtablissementSerializer),
         name='token_refresh'),
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/logout/', LogoutView.as_view(), name='logout'),
    path('api/auth/activer-compte/', ActiverCompteView.as_view(), name='activer-compte'),
    path('api/auth/renvoyer-activation/', RenvoyerActivationView.as_view(), name='renvoyer-activation'),
    
    path('api/auth/verifier-jeton/<str:jeton>/', VerifierJetonView.as_view(), name='verifier-jeton'),
    path('api/auth/definir-mot-de-passe/', DefinirMotDePasseView.as_view(), name='definir-mot-de-passe'),
    path('api/auth/changer-mot-de-passe/', ChangerMotDePasseView.as_view(), name='changer-mot-de-passe'),
    path('api/auth/mot-de-passe-oublie/', MotDePasseOublieView.as_view(), name='mot-de-passe-oublie'),
    
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
    path('api/organisations/', include('apps.organisations.urls')),
    
]

if settings.ENABLE_API_DOCS:
    # La documentation Swagger décrit toute la surface de l'API : utile en
    # développement, mais inutile de la publier en production.
    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    ]

# Fichiers envoyés par les utilisateurs (logos, photos, manuels PDF).
# static() ne sert rien hors DEBUG, d'où la vue serve() en production.
# WhiteNoise ne gère que les fichiers statiques. Suffisant pour ce volume ;
# à plus grande échelle, confier /media/ à Nginx ou à un stockage objet.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]

