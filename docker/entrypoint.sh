#!/bin/sh
# Ce script tourne à chaque démarrage du conteneur backend.
set -e

echo "Attente de la base de données Postgres..."
# On essaie de se connecter à Postgres toutes les secondes, jusqu'à ce
# que ce soit prêt (au premier démarrage, Postgres met quelques secondes
# à être disponible).
python -c "
import os, sys, time
import psycopg2 as psycopg

url = os.environ.get('DATABASE_URL', '')
if url:
    for _ in range(30):
        try:
            psycopg.connect(url.replace('postgres://', 'postgresql://'), connect_timeout=2).close()
            break
        except Exception:
            time.sleep(1)
    else:
        sys.exit('Impossible de joindre la base de données.')
"
echo "Base de données prête."

echo "Application des migrations..."
python manage.py migrate --noinput

echo "Collecte des fichiers statiques..."
python manage.py collectstatic --noinput

echo "Démarrage du serveur..."
# --reload : redémarre automatiquement quand un fichier .py change (pratique
# en dev grâce au volume monté sur le code). À retirer en production.
# --timeout 75 : le proxy vers le service IA (chatbot) peut attendre
# jusqu'à 60s une réponse du LLM (voir gerant/views.py ChatbotView) ; le
# timeout par défaut de Gunicorn (30s) tuait le worker avant la fin de
# cette requête, provoquant une 500 côté frontend.
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 75
