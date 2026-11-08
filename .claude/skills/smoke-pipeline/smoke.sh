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
#   * Os usuários (a gestora, o motorista e a silver) nascem pelo `flask create-user`, com senha
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
Depois, o caos: consumer parado (e parado de novo no meio da drenagem), RabbitMQ parado
e Mongo parado, com a contagem final: aceitos no 202 = documentos no bronze.

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
# O perfil entra aqui para o `down` levar também o Prometheus e o Grafana.
P=(docker compose -p "$projeto" --profile observabilidade -f docker-compose.yml -f "$pasta/compose.override.yml")
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
  flask create-user --name smoke-motorista --password "$SMOKE_SENHA" --role usuario &&
  flask create-user --name smoke-silver --password "$SMOKE_SENHA" --role silver
' >"$tmp/usuarios.log" 2>&1; then
  echo "FALHOU  flask create-user:"
  tail -n 20 "$tmp/usuarios.log" | sed 's/^/        /'
  echo "RESULTADO: FALHOU — flask create-user"
  exit 1
fi
echo ">> flask create-user: smoke-gestora e smoke-motorista (usuario) e smoke-silver (silver)"

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
  grep -qE '"status": 5[0-9][0-9]|Traceback'; then
  echo "FALHOU  houve 5xx ou traceback nos logs"
  resultado=1
else
  echo "OK      nenhum 5xx nem traceback nos logs do api, do bronze-api e do consumer"
fi

# O pronto da Fase 6: um grep pelo request_id acha a requisição nos dois processos.
marca="\"request_id\": \"smoke-pipeline-$execucao\""
no_api="$("${P[@]}" logs --no-log-prefix api 2>&1 | grep -F "$marca" | grep -c '"event": "http.access"')"
no_consumer="$("${P[@]}" logs --no-log-prefix consumer 2>&1 | grep -F "$marca" | grep -c '"event": "bronze.batch.ingested"')"
if [ "$no_api" -ge 1 ] && [ "$no_consumer" -ge 1 ]; then
  echo "OK      o request_id do lote aparece no log do api ($no_api) e no do consumer ($no_consumer)"
else
  echo "FALHOU  request_id nos logs: api $no_api, consumer $no_consumer"
  resultado=1
fi

echo ">> observabilidade: Prometheus coletando os processos"
if "${P[@]}" up -d --wait --wait-timeout 120 --no-deps prometheus >"$tmp/prometheus.log" 2>&1; then
  "${P[@]}" exec -T api python - <"$pasta/observabilidade.py" || resultado=1
else
  echo "FALHOU  o Prometheus não subiu:"
  tail -n 10 "$tmp/prometheus.log" | sed 's/^/        /'
  resultado=1
fi

# --- O caos ---------------------------------------------------------------------

# Muitos, para a drenagem durar bem mais que o `docker compose stop` leva para chegar.
lotes_do_sigterm=600

caos() {
  "${P[@]}" exec -T -e SMOKE_SENHA="$senha" api python - "$@" <"$pasta/caos.py" || resultado=1
}

# Sobe o serviço de volta e espera ele ficar saudável; o resto do projeto fica como está.
voltar() {
  "${P[@]}" up -d --wait --wait-timeout 180 --no-deps "$@" >"$tmp/voltar.log" 2>&1 || {
    echo "FALHOU  $* não voltou:"
    tail -n 10 "$tmp/voltar.log" | sed 's/^/        /'
    resultado=1
  }
}

# O código HTTP de /health/ready do consumer, ou 000 se ele não responder.
pronto() {
  "${P[@]}" exec -T consumer python -c "
import urllib.error, urllib.request
try:
    print(urllib.request.urlopen('http://localhost:8081/health/ready', timeout=4).status)
except urllib.error.HTTPError as erro:
    print(erro.code)
" 2>/dev/null | tr -d '\r' || echo 000
}

# Quantos lotes o consumer gravou até agora, pelo log dele.
gravados() {
  "${P[@]}" logs --no-log-prefix consumer 2>/dev/null | grep -c 'gravado no bronze'
}

# Espera /health/ready dar o código $1, até $2 segundos.
esperar_pronto() {
  local fim=$(($(date +%s) + $2))
  while [ "$(date +%s)" -lt "$fim" ]; do
    [ "$(pronto)" = "$1" ] && return 0
    sleep 2
  done
  return 1
}

confere_pronto() {
  if esperar_pronto "$1" "$2"; then
    echo "OK      $3"
  else
    echo "FALHOU  $3: /health/ready respondeu '$(pronto)'"
    resultado=1
  fi
}

if [ "$resultado" -eq 0 ]; then
  echo ">> caos: consumer parado, a fila acumula"
  "${P[@]}" stop consumer >/dev/null 2>&1
  caos enviar "$lotes_do_sigterm"

  echo ">> caos: o consumer volta e leva SIGTERM no meio da drenagem"
  antes="$(gravados)"
  "${P[@]}" start consumer >/dev/null 2>&1
  # Para assim que o primeiro lote aparece no log, com o resto ainda na fila.
  fim=$(($(date +%s) + 60))
  while [ "$(gravados)" -le "$antes" ] && [ "$(date +%s)" -lt "$fim" ]; do sleep 0.2; done
  "${P[@]}" stop consumer >/dev/null 2>&1
  drenados=$(($(gravados) - antes))
  if [ "$drenados" -gt 0 ] && [ "$drenados" -lt "$lotes_do_sigterm" ]; then
    echo "OK      SIGTERM no meio da drenagem: parou depois de $drenados de $lotes_do_sigterm lotes"
  else
    echo "FALHOU  o SIGTERM não caiu no meio da drenagem: $drenados de $lotes_do_sigterm lotes"
    resultado=1
  fi
  voltar consumer

  echo ">> caos: RabbitMQ parado"
  "${P[@]}" stop rabbitmq >/dev/null 2>&1
  caos rabbit-fora
  confere_pronto 503 45 "broker fora: o consumer fica de pé e não pronto"
  voltar rabbitmq
  confere_pronto 200 90 "o broker voltou: o consumer reconectou sozinho"
  caos enviar 5

  echo ">> caos: Mongo parado"
  "${P[@]}" stop mongo >/dev/null 2>&1
  caos mongo-fora 5
  confere_pronto 503 60 "Mongo fora: o consumer fica de pé e não pronto"
  voltar mongo
  confere_pronto 200 90 "o Mongo voltou: o consumer está pronto de novo"

  echo ">> caos: a contagem"
  caos conferir

  # O comando de operação, como o README manda rodar, com a DLQ que deve ter sobrado.
  dlq_lista="$("${P[@]}" run --rm -T --no-deps --entrypoint flask setup rabbitmq dlq-list 2>&1)"
  if grep -q "A DLQ está vazia" <<<"$dlq_lista"; then
    echo "OK      flask rabbitmq dlq-list: a DLQ está vazia depois do caos"
  else
    echo "FALHOU  flask rabbitmq dlq-list:"
    tail -n 10 <<<"$dlq_lista" | sed 's/^/        /'
    resultado=1
  fi

  # Os 503 agora são esperados; 500 e traceback, não.
  erros="$("${P[@]}" logs --no-log-prefix api bronze-api consumer 2>&1 |
    grep -E -A12 '"status": 50[0124-9]|"status": 5[1-9][0-9]|Traceback')"
  if [ -n "$erros" ]; then
    echo "FALHOU  houve erro interno ou traceback nos logs durante o caos:"
    head -n 30 <<<"$erros" | cut -c1-200 | sed 's/^/        /'
    resultado=1
  else
    echo "OK      nenhum erro interno nem traceback nos logs durante o caos"
  fi
fi

if [ "$resultado" -ne 0 ]; then
  logs_dos_processos
  echo "RESULTADO: FALHOU"
  exit 1
fi
echo ">> o consumer, em resumo:"
"${P[@]}" logs --no-log-prefix consumer 2>&1 | grep -E 'gravado no bronze' | sed 's/^/        /'
echo "RESULTADO: OK"
