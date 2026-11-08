#!/bin/sh
# Job de preparação: deixa os armazenamentos prontos antes de os processos subirem.
#
# Roda uma vez por `docker compose up` (os processos esperam por ele com
# `service_completed_successfully`) e é idempotente: rodar de novo não muda o que
# já está no lugar. Migra o Postgres, aplica a topologia do RabbitMQ e os índices
# do bronze no Mongo, e cria os usuários dos outros processos nos dois.
set -e

cd /srv

echo "[setup] aplicando migrações..."
alembic upgrade head

echo "[setup] aplicando a topologia e os usuários do RabbitMQ..."
flask rabbitmq setup

echo "[setup] aplicando os índices, a retenção e os usuários do bronze..."
flask mongo setup

echo "[setup] pronto."
