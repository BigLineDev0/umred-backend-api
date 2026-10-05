#!/bin/sh
# Point d'entrée du conteneur backend.
# Les migrations ne s'appliquent que si MIGRER_AU_DEMARRAGE=1 : un seul
# service (le backend web) migre, pas le planificateur qui partage l'image.
set -e

if [ "${MIGRER_AU_DEMARRAGE:-0}" = "1" ]; then
    echo "Application des migrations..."
    python manage.py migrate --noinput
fi

exec "$@"
