#!/bin/sh
set -eu

echo "Running database migrations..."
# 迁移失败必须退出，不能让应用跑在错误的 schema 上
if ! alembic upgrade head; then
    echo "ERROR: Database migration failed. Aborting startup." >&2
    exit 1
fi

echo "Migrations applied successfully."
exec "$@"
