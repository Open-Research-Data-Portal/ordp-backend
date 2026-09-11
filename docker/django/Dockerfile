FROM python:3.12-slim-bookworm

WORKDIR /app

# System deps needed for psycopg2 and general builds
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt /app/requirements/base.txt
RUN pip install --no-cache-dir -r requirements/base.txt

COPY . /app

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --noinput --settings=${DJANGO_SETTINGS_MODULE:-config.settings.production} && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 3 --worker-class gthread --threads 4 --timeout 60 --graceful-timeout 30"]