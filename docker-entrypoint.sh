#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head || echo "Migration failed, continuing anyway..."

exec "$@"
