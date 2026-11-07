#!/bin/sh
# Executado uma única vez, na primeira inicialização do volume do Postgres.
# Cria o banco usado pela suíte de testes ao lado do banco da aplicação, para
# que `pytest` funcione sem nenhum passo manual de preparação.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE "${POSTGRES_DB}_test" OWNER "$POSTGRES_USER";
EOSQL

echo "[initdb] banco de testes '${POSTGRES_DB}_test' criado."
