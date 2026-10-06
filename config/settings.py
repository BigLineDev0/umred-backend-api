import environ
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')

# Valeurs par défaut sûres : sans .env, le serveur refuse de démarrer
# (pas de SECRET_KEY) et tourne hors mode debug. Le mode debug et les hôtes
# autorisés doivent être activés explicitement dans le .env.
SECRET_KEY = env('SECRET_KEY')

DEBUG = env.bool('DEBUG', default=False)

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Tiers
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'drf_spectacular',

    # Apps SenLab
    'apps.organisations',
    'apps.utilisateurs',
    'apps.laboratoires',
    'apps.equipements',
    'apps.reservations',
    'apps.maintenances',
    'apps.notifications',
    'apps.core',
    'apps.consommables',
    'apps.projets',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

INTERNAL_IPS = ['127.0.0.1']

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

AUTH_USER_MODEL = 'utilisateurs.Utilisateur'

DATABASES = {
    'default': env.db('DATABASE_URL', default='postgres://umred:umred@localhost:5432/umred_labo')
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Dakar'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Fichiers envoyés (photos, logos, manuels PDF) sur un stockage compatible
# S3 (Supabase Storage, Cloudflare R2, AWS...) dès que S3_BUCKET est défini.
# Indispensable sur un hébergement au disque éphémère (Render gratuit) :
# sans cela, les fichiers disparaissent à chaque redéploiement ou mise en veille.
if env('S3_BUCKET', default=''):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env('S3_BUCKET'),
            "endpoint_url": env('S3_ENDPOINT_URL'),
            "access_key": env('S3_ACCESS_KEY_ID'),
            "secret_key": env('S3_SECRET_ACCESS_KEY'),
            "region_name": env('S3_REGION', default='auto'),
            # Bucket public : URL directe et stable, sans signature qui expire.
            # Ex. Supabase : <projet>.supabase.co/storage/v1/object/public/<bucket>
            "custom_domain": env('S3_DOMAINE_PUBLIC', default=None),
            "querystring_auth": False,
            "file_overwrite": False,
            "addressing_style": "path",
            "signature_version": "s3v4",
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Django REST Framework ---
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        # JWT + contrôle à chaque requête : compte actif, établissement non suspendu.
        'apps.utilisateurs.authentication.JWTAuthenticationEtablissement',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # Anti brute-force : les requêtes anonymes sont limitées globalement,
    # et les vues d'authentification (throttle_scope = 'auth') plus
    # strictement encore. Compteurs par IP (anonyme) ou par utilisateur.
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/min',
        'auth': '10/min',
    },
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'SenLab API',
    'DESCRIPTION': "API de gestion des plannings, équipements et maintenance",
    'VERSION': '1.0.0',
}

# -- Dure des tokens
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# --- CORS (pour Angular en localhost:4200) ---
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=['http://localhost:4200'])

# --- Celery / Redis ---
CELERY_BROKER_URL = env('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env('REDIS_URL', default='redis://localhost:6379/0')

# --- Envoi mail pour l'instant dans la console
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='SenLab <no-reply@senlab.sn>')

# Envoi par API HTTP (Brevo) : à utiliser là où les ports SMTP sont bloqués
# (Render gratuit bloque 25, 465 et 587). Activé avec
# EMAIL_BACKEND=anymail.backends.brevo.EmailBackend et BREVO_API_KEY.
ANYMAIL = {'BREVO_API_KEY': env('BREVO_API_KEY', default='')}

FRONTEND_URL = env('FRONTEND_URL', default='http://localhost:4200')

# Service IA (FastAPI) : rédige la synthèse hebdomadaire en langage naturel.
IA_SERVICE_URL = env('IA_SERVICE_URL', default='http://localhost:8001/api')

# Secret partagé avec n8n pour déclencher les tâches planifiées
# (/api/taches/...). Vide = tâches désactivées.
TACHES_TOKEN = env('TACHES_TOKEN', default='')

# Webhook n8n qui envoie les emails de réservation. Vide = envoi désactivé.
# Vide par défaut = pas d'envoi : une URL « webhook-test » ne répond que
# lorsque l'éditeur n8n est ouvert, elle ne doit jamais servir en production.
N8N_WEBHOOK_URL = env('N8N_WEBHOOK_URL', default='')

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Origines autorisées pour les formulaires POST (admin Django) servis
# derrière un proxy : ex. http://localhost:8080 en Docker.
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# --- Sécurité production ---
if not DEBUG:
    # Redirection HTTPS par Django. À désactiver (SECURE_SSL_REDIRECT=False)
    # quand un proxy fait déjà la redirection ou quand d'autres services
    # appellent Django en HTTP interne (ex. le service IA dans Docker :
    # http://backend:8000), sinon ces appels seraient redirigés et échoueraient.
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)

    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # Cookies réservés à HTTPS ; à désactiver uniquement pour un essai
    # local en HTTP (sinon impossible de se connecter à l'admin Django).
    SESSION_COOKIE_SECURE = env.bool('COOKIES_SECURISES', default=True)
    CSRF_COOKIE_SECURE = env.bool('COOKIES_SECURISES', default=True)

    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')