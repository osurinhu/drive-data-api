#!/bin/sh
# Job de preparação: deixa os armazenamentos prontos antes de os processos subirem.
#
# Roda uma vez por `docker compose up` (os processos esperam por ele com
# `service_completed_successfully`) e é idempotente: rodar de novo não muda o que
# já está no lugar. Por enquanto só migra o Postgres; a topologia do RabbitMQ
# (`flask rabbitmq setup`) e os índices do Mongo (`flask mongo setup`) entram aqui
# quando existirem.
set -e

cd /srv

echo "[setup] aplicando migrações..."
alembic upgrade head

echo "[setup] pronto."
