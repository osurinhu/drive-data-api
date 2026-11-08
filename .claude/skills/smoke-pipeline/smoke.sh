#!/usr/bin/env bash
# Smoke do pipeline com os processos reais: api, consumer e bronze-api de pé, na
# imagem de produção, conferidos de ponta a ponta por HTTP.
#
# Por que assim:
#   * Um projeto do compose à parte (smoke-pipeline), com o override desta pasta:
#     volumes próprios (Postgres, Mongo e RabbitMQ começam vazios e são apagados no
#     fim) e nenhuma porta no host. O ambiente de desenvolvimento fica intacto, de pé
#     ou não.
#   * A subida é a de produção: o job `setup` migra, aplica a topologia e os índices,
#     e só então sobem o api, o bronze-api e o consumer, cada um no seu perfil.
#   * O roteiro HTTP (fluxo.py) roda dentro do container do api, só com a stdlib, e
#     alcança o bronze-api pela rede do projeto: nada é publicado no host.
#   * Os usuários (a gestora e a silver) nascem pelo `flask create-user`, com senha
#     aleatória que só passa por variável de ambiente.
#   * O .env é lido pelo compose, como no `docker compose up`, e nunca impresso.

set -uo pipefail

# Git Bash (Windows): sem isto, argumentos como `/srv` viram `C:/Program Files/Git/srv`.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

uso() {
  cat <<'USO'
Uso (a partir da raiz do repositório):
  bash .claude/skills/smoke-pipeline/smoke.sh

Sobe o pipeline inteiro num projeto do compose à parte (smoke-pipeline): db, mongo,
rabbitmq, o job setup, o api, o bronze-api e o consumer, com o código atual. Confere o
health de cada processo, a matriz da leitura (401, 403, 404, 422), o lote que vai do
app à silver sem perda, o reenvio sem repetição e a página vazia com o mesmo cursor.

Saída: 0 = tudo OK; 1 = alguma verificação falhou (ou um processo não subiu);
       2 = infraestrutura (Docker parado, build quebrado).
USO
}

case "${1:-}" in
  "") ;;
  -h | --help | ajuda) uso; exit 0 ;;
  *) echo "Argumento desconhecido: $1" >&2; uso >&2; exit 2 ;;
esac

raiz="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "INFRA: rode dentro do repositório." >&2
  exit 2
}
cd "$raiz" || exit 2

pasta=".claude/skills/smoke-pipeline"
projeto="smoke-pipeline"
P=(docker compose -p "$projeto" -f docker-compose.yml -f "$pasta/compose.override.yml")
tmp="$(mktemp -d)"

limpar() {
  # Os volumes e as imagens do projeto vão junto: nada do smoke sobra no Docker.
  "${P[@]}" down -v --remove-orphans --rmi local >/dev/null 2>&1
  rm -rf "$tmp"
}
trap limpar EXIT
trap 'exit 130' INT TERM

falha_infra() {
  echo "INFRA: $1" >&2
  if [ -n "${2:-}" ] && [ -s "$2" ]; then tail -n 40 "$2" >&2; fi
  exit 2
}

logs_dos_processos() {
  for servico in setup api bronze-api consumer; do
    echo ">> fim do log do $servico:"
    "${P[@]}" logs --no-log-prefix --tail 25 "$servico" 2>&1 | sed 's/^/        /'
  done
}

docker version --format '{{.Server.Version}}' >/dev/null 2>&1 ||
  falha_infra "Docker indisponível — abra o Docker Desktop e rode de novo."

# Sobra de uma execução interrompida: tudo começa do zero.
"${P[@]}" down -v --remove-orphans >/dev/null 2>&1

echo ">> build das imagens com o código atual..."
"${P[@]}" build -q setup api bronze-api consumer >"$tmp/build.log" 2>&1 ||
  falha_infra "o build das imagens falhou." "$tmp/build.log"

echo ">> subindo db, mongo e rabbitmq → setup → api, bronze-api e consumer (projeto $projeto)..."
inicio="$(date +%s)"
if ! "${P[@]}" up -d --wait --wait-timeout 240 api bronze-api consumer >"$tmp/up.log" 2>&1; then
  echo "FALHOU  a subida dos processos:"
  tail -n 20 "$tmp/up.log" | sed 's/^/        /'
  logs_dos_processos
  echo "RESULTADO: FALHOU — um processo não subiu"
  exit 1
fi
echo ">> no ar em $(($(date +%s) - inicio)) s"
"${P[@]}" logs --no-log-prefix setup 2>&1 | grep -E '^\[setup\]|Topologia|Índices' | sed 's/^/        /'

senha="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
if ! "${P[@]}" exec -T -e SMOKE_SENHA="$senha" api sh -c '
  flask create-user --name smoke-gestora --password "$SMOKE_SENHA" --role usuario &&
  flask create-user --name smoke-silver --password "$SMOKE_SENHA" --role silver
' >"$tmp/usuarios.log" 2>&1; then
  echo "FALHOU  flask create-user:"
  tail -n 20 "$tmp/usuarios.log" | sed 's/^/        /'
  echo "RESULTADO: FALHOU — flask create-user"
  exit 1
fi
echo ">> flask create-user: smoke-gestora (usuario) e smoke-silver (silver)"

echo ">> roteiro (fluxo.py, dentro do container do api)"
execucao="$(date +%Y%m%d%H%M%S)"
"${P[@]}" exec -T -e SMOKE_SENHA="$senha" api python - "$execucao" <"$pasta/fluxo.py"
resultado=$?

# A fila: o que entrou saiu, e nada foi para a DLQ.
filas="$("${P[@]}" exec -T rabbitmq gosu rabbitmq rabbitmqctl -q list_queues -p drive-data \
  --no-table-headers name messages 2>/dev/null | tr -d '\r')"
fila="$(awk '$1 == "bronze.telemetry" { print $2 }' <<<"$filas")"
dlq="$(awk '$1 == "bronze.telemetry.dlq" { print $2 }' <<<"$filas")"
if [ "$fila" = "0" ] && [ "$dlq" = "0" ]; then
  echo "OK      fila bronze.telemetry vazia e DLQ vazia"
else
  echo "FALHOU  fila: '${fila:-?}' mensagem(ns) em bronze.telemetry, '${dlq:-?}' na DLQ"
  resultado=1
fi

# Nenhum 5xx nos processos HTTP, nem traceback no consumer.
if "${P[@]}" logs --no-log-prefix api bronze-api consumer 2>&1 |
  grep -qE '" 5[0-9][0-9] |Traceback'; then
  echo "FALHOU  houve 5xx ou traceback nos logs"
  resultado=1
else
  echo "OK      nenhum 5xx nem traceback nos logs do api, do bronze-api e do consumer"
fi

if [ "$resultado" -ne 0 ]; then
  logs_dos_processos
  echo "RESULTADO: FALHOU"
  exit 1
fi
echo ">> o consumer, em resumo:"
"${P[@]}" logs --no-log-prefix consumer 2>&1 | grep -E 'gravado no bronze' | sed 's/^/        /'
echo "RESULTADO: OK"
