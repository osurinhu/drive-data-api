#!/bin/sh
# Aplica as migrações pendentes e entrega o controle ao comando do container.
set -e

cd /srv

echo "[entrypoint] aplicando migrações..."
alembic upgrade head

echo "[entrypoint] iniciando: $*"
exec "$@"
