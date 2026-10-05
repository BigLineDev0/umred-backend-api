# ---------- Étape 1 : dépendances ----------
# Les roues (wheels) sont compilées dans une étape séparée : l'image finale
# ne contient ni compilateur ni en-têtes de développement.
FROM python:3.12-slim AS dependances

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# ---------- Étape 2 : image d'exécution ----------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY --from=dependances /wheels /wheels
RUN pip install /wheels/* && rm -rf /wheels

# Utilisateur sans privilèges : une faille dans l'application ne donne pas
# les droits root sur le conteneur.
RUN useradd --create-home --uid 1000 umred
COPY --chown=umred:umred . .
RUN mkdir -p /app/media /app/staticfiles && chown -R umred:umred /app/media /app/staticfiles \
    && chmod +x docker/entrypoint.sh

USER umred

# Fichiers statiques (admin Django, Swagger) collectés au build ; une clé
# factice suffit, collectstatic ne signe rien.
RUN SECRET_KEY=build-uniquement DATABASE_URL=sqlite:////tmp/build.db python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-"]
