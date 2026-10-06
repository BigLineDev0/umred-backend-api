# Image de base : Python léger
FROM python:3.12-slim

# Configuration Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dépendances système nécessaires pour PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie tout le projet
# Cela copie également docker/entrypoint.sh
COPY . .

# Rend l'entrypoint exécutable
RUN chmod +x /app/docker/entrypoint.sh

EXPOSE 8000

# Script de démarrage
ENTRYPOINT ["/app/docker/entrypoint.sh"]
