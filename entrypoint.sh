#!/bin/sh
set -e

echo "Waiting for MySQL at ${MYSQL_HOST}:${MYSQL_PORT}..."
until mysqladmin ping -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" --skip-ssl --silent; do
  sleep 1
done
echo "MySQL is up."

python manage.py migrate --noinput

mkdir -p /app/logs
touch /app/logs/minimo_sync.log
service cron start

exec "$@"
