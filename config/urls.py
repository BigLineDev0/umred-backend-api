from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenRefreshView
from apps.utilisateurs.views import ChangerMotDePasseView, LogoutView, MonProfilView

from apps.utilisateurs.views import RegisterView, UmredTokenObtainPairView, VerifierJetonView, DefinirMotDePasseView, MotDePasseOublieView
from apps.utilisateurs.views import ActiverCompteView, RenvoyerActivationView


from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),

    path('api/auth/login/', UmredTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
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

if settings.DEBUG:
    # La documentation Swagger décrit toute la surface de l'API : utile en
    # développement, mais inutile de la publier en production.
    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    ]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # static() ne fait rien quand DEBUG=False : sans cette route, les photos
    # de profil et manuels PDF seraient introuvables en production.
    # WhiteNoise ne sert que les fichiers statiques, pas les fichiers envoyés
    # par les utilisateurs. Suffisant pour ce volume ; à grande échelle, on
    # confierait /media/ au serveur web (Nginx) ou à un stockage objet.
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]

