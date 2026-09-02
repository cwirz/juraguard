#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --worker-class gthread \
    --workers "${WEB_CONCURRENCY:-2}" --threads "${WEB_THREADS:-4}" --timeout 30 --access-logfile /dev/null
