#!/bin/sh
# Aplica as migrações pendentes (se RUN_MIGRATIONS=1, o padrão) e entrega o
# controle ao comando do container.
#
# No docker compose quem migra é o job `setup` (docker/setup.sh), e os processos
# sobem depois dele com RUN_MIGRATIONS=0: com o `api` e o `bronze-api` migrando ao
# mesmo tempo, os dois disputariam o mesmo banco vazio. O padrão 1 fica para o
# container avulso (como o do /smoke-api), que continua migrando sozinho.
set -e

cd /srv

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] aplicando migrações..."
  alembic upgrade head
fi

echo "[entrypoint] iniciando: $*"
exec "$@"
