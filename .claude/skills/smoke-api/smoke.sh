#!/usr/bin/env bash
# Smoke da API real: o código atual, na imagem de produção (entrypoint com
# migrações + gunicorn), conferido por HTTP de ponta a ponta.
#
# Por que assim:
#   * Container avulso (`docker compose run api`) num banco descartável: os dados
#     do banco de desenvolvimento e o `api` que já está rodando ficam intactos.
#   * O banco descartável entra pela própria interpolação do compose
#     (POSTGRES_DB=<nome>_smoke): o .env não é lido e nenhum segredo passa por aqui.
#   * As migrações rodam do zero, pelo entrypoint de verdade (RUN_MIGRATIONS=1: no
#     compose quem migra é o job `setup`, que o container avulso não usa): se a
#     aplicação não sobe num banco vazio, o smoke falha antes da primeira requisição.
#   * O perfil é o do serviço `api` do compose (FLASK_PROCESS=api): o health precisa
#     mostrar o banco e o RabbitMQ, e não o Mongo, que esse processo não usa.
#   * O RabbitMQ também é descartável: um vhost próprio (RABBITMQ_VHOST, outra
#     interpolação do compose), preparado pelo `flask rabbitmq setup` de verdade, no
#     serviço `setup`, e apagado no fim. A fila do desenvolvimento não recebe nada.
#   * Cada requisição leva um X-Request-ID próprio (smoke-<execução>-NN): a falha
#     vem acompanhada das linhas do log com o mesmo id.
#   * Um usuário por papel, criado pelo `flask create-user` com senha aleatória.
#     Tudo some junto com o banco e o container, mesmo se o roteiro for interrompido.

set -uo pipefail

# Git Bash (Windows): sem isto, argumentos como `/srv` viram `C:/Program Files/Git/srv`.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

uso() {
  cat <<'USO'
Uso (a partir da raiz do repositório):
  bash .claude/skills/smoke-api/smoke.sh

Sobe o código atual num container avulso do `api`, com banco descartável, e
confere por HTTP: health (com o perfil api: banco e RabbitMQ), OpenAPI, cadastro, login,
renovação e logout, CRUD de veículo, os motoristas, o envio de lote de telemetria (202 e a
mensagem na fila),
a matriz 401/403/404/409/413/422 e o X-Request-ID no header, no envelope de erro e no log
de acesso.

Saída: 0 = tudo OK; 1 = alguma verificação falhou (ou a aplicação não subiu);
       2 = infraestrutura (Docker parado, build quebrado, banco fora do ar).
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

container="base-api-smoke"
vhost="drive-data-smoke"
tmp="$(mktemp -d)"
# curl é programa nativo no Windows: os arquivos dele precisam de caminho C:/...
if command -v cygpath >/dev/null 2>&1; then tmp="$(cygpath -m "$tmp")"; fi
banco=""
broker_no_ar=0

# rabbitmqctl dentro do container do broker, como o usuário `rabbitmq` (o cookie
# do Erlang é dele; ver o healthcheck no compose). Não precisa de senha nenhuma.
rabbitmqctl() {
  docker compose exec -T rabbitmq gosu rabbitmq rabbitmqctl -q "$@"
}

limpar() {
  docker rm -f "$container" >/dev/null 2>&1
  if [ "$broker_no_ar" -eq 1 ]; then
    rabbitmqctl delete_vhost "$vhost" >/dev/null 2>&1
  fi
  if [ -n "$banco" ]; then
    docker compose exec -T db sh -s -- "$banco" >/dev/null 2>&1 <<'APAGAR_BANCO' ||
psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$1\" WITH (FORCE)"
APAGAR_BANCO
      echo "aviso: não consegui apagar o banco descartável $banco." >&2
  fi
  rm -rf "$tmp"
}
trap limpar EXIT
trap 'exit 130' INT TERM

falha_infra() {
  echo "INFRA: $1" >&2
  if [ -n "${2:-}" ] && [ -s "$2" ]; then tail -n 40 "$2" >&2; fi
  exit 2
}

# Falha da aplicação antes da matriz: mostra o fim do log do container.
falha_app() {
  echo "FALHOU  $1. Fim do log do container:"
  docker logs --tail 40 "$container" 2>&1 | sed 's/^/        /'
  echo "RESULTADO: FALHOU — $1"
  exit 1
}

docker version --format '{{.Server.Version}}' >/dev/null 2>&1 ||
  falha_infra "Docker indisponível — abra o Docker Desktop e rode de novo."

echo ">> build das imagens do api e do setup com o código atual..."
docker compose build -q api setup >"$tmp/build.log" 2>&1 ||
  falha_infra "o build das imagens falhou." "$tmp/build.log"

docker compose up -d --wait db rabbitmq >"$tmp/db.log" 2>&1 ||
  falha_infra "o banco ou o RabbitMQ não subiu." "$tmp/db.log"
broker_no_ar=1

# Sobra de uma execução interrompida: a fila precisa começar vazia.
rabbitmqctl delete_vhost "$vhost" >/dev/null 2>&1
echo ">> flask rabbitmq setup no vhost descartável $vhost..."
if ! RABBITMQ_VHOST="$vhost" docker compose run --rm --no-deps -T --entrypoint flask \
  setup rabbitmq setup >"$tmp/topologia.log" 2>&1; then
  echo "FALHOU  flask rabbitmq setup:"
  tail -n 20 "$tmp/topologia.log" | sed 's/^/        /'
  echo "RESULTADO: FALHOU — flask rabbitmq setup"
  exit 1
fi

nome="$(docker compose exec -T db printenv POSTGRES_DB 2>/dev/null | tr -d '\r')"
[ -n "$nome" ] || falha_infra "não consegui ler POSTGRES_DB do container db."
banco="${nome}_smoke"

docker compose exec -T db sh -s -- "$banco" >"$tmp/banco.log" 2>&1 <<'CRIAR_BANCO' ||
psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS \"$1\" WITH (FORCE)" \
  -c "CREATE DATABASE \"$1\" OWNER \"$POSTGRES_USER\""
CRIAR_BANCO
  falha_infra "não consegui criar o banco descartável $banco." "$tmp/banco.log"

docker rm -f "$container" >/dev/null 2>&1 # sobra de uma execução interrompida
echo ">> container avulso do api no banco $banco (migrações do zero)..."
POSTGRES_DB="$banco" RABBITMQ_VHOST="$vhost" docker compose run -d -T --no-deps --name "$container" \
  -e RUN_MIGRATIONS=1 --publish 127.0.0.1::5000 api >"$tmp/run.log" 2>&1 ||
  falha_infra "o docker compose run do api falhou." "$tmp/run.log"

porta="$(docker port "$container" 5000/tcp 2>/dev/null | head -n 1)"
porta="${porta##*:}"
[ -n "$porta" ] || falha_infra "o container avulso não publicou a porta 5000."
base="http://127.0.0.1:$porta/api/v1"

inicio="$(date +%s)"
until [ "$(curl -s -o /dev/null -w '%{http_code}' "$base/health/")" = "200" ]; do
  [ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" = "true" ] ||
    falha_app "a aplicação não subiu no banco vazio"
  [ $(($(date +%s) - inicio)) -lt 90 ] ||
    falha_app "o health não respondeu 200 em 90 s"
  sleep 1
done
echo ">> no ar em $(($(date +%s) - inicio)) s, em $base"

senha="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
if ! docker exec -e SMOKE_SENHA="$senha" "$container" sh -c '
  for papel in usuario admin silver; do
    flask create-user --name "smoke-$papel" --password "$SMOKE_SENHA" --role "$papel" || exit 1
  done
  flask create-user --name smoke-motorista --password "$SMOKE_SENHA" --role usuario
' >"$tmp/usuarios.log" 2>&1; then
  echo "FALHOU  flask create-user:"
  tail -n 20 "$tmp/usuarios.log" | sed 's/^/        /'
  echo "RESULTADO: FALHOU — flask create-user"
  exit 1
fi
echo ">> flask create-user: smoke-usuario, smoke-motorista, smoke-admin e smoke-silver"

# --- Matriz ---------------------------------------------------------------------

execucao="$(date +%Y%m%d%H%M%S)"
n=0
total=0
falhas=0
erro_servidor=0
ids_com_falha=()

# requisicao <método> <caminho> [token] [corpo JSON]: define $status e $rid.
# Corpo e headers da resposta ficam em $tmp/corpo e $tmp/headers; o corpo enviado
# vai por arquivo, para nenhuma aspa atravessar a linha de comando do Windows.
requisicao() {
  n=$((n + 1))
  rid="smoke-$execucao-$(printf '%02d' "$n")"
  local args=(-s -o "$tmp/corpo" -D "$tmp/headers" -w '%{http_code}'
    -X "$1" -H "X-Request-ID: $rid")
  if [ -n "${3:-}" ]; then args+=(-H "Authorization: Bearer $3"); fi
  if [ -n "${4:-}" ]; then
    printf '%s' "$4" >"$tmp/envio.json"
    args+=(-H 'Content-Type: application/json' --data-binary "@$tmp/envio.json")
  fi
  status="$(curl "${args[@]}" "$base$2")" || status="000"
}

# confere <status esperado> <descrição> <método> <caminho> [token] [corpo]
confere() {
  local esperado="$1" descricao="$2"
  shift 2
  requisicao "$@"
  total=$((total + 1))
  if [ "$status" = "$esperado" ]; then
    printf 'OK      %s  %-6s %-14s %s\n' "$status" "$1" "$2" "$descricao"
    return 0
  fi
  falhas=$((falhas + 1))
  ids_com_falha+=("$rid")
  case "$status" in 5* | 000) erro_servidor=1 ;; esac
  printf 'FALHOU  %s  %-6s %-14s %s — esperado %s (req=%s)\n' \
    "$status" "$1" "$2" "$descricao" "$esperado" "$rid"
  printf '        corpo: %s\n' "$(head -c 300 "$tmp/corpo" 2>/dev/null)"
  return 1
}

# campo <nome>: valor do campo no último corpo JSON (resposta compacta; sem jq).
campo() {
  sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",}]*\).*/\1/p" "$tmp/corpo" | head -n 1
}

echo ">> matriz (cada requisição com X-Request-ID smoke-$execucao-NN)"
confere 200 "health, público" GET /health/

# O perfil do processo, visto de fora: uma chave por dependência que o api usa.
total=$((total + 1))
if [ "$(campo database)" = "ok" ] && [ "$(campo rabbitmq)" = "ok" ] && [ -z "$(campo mongo)" ]; then
  echo "OK      health do perfil api: database e rabbitmq ok, sem mongo"
else
  falhas=$((falhas + 1))
  echo "FALHOU  health do perfil api: $(head -c 300 "$tmp/corpo")"
fi

confere 200 "liveness, público" GET /health/live
confere 200 "spec OpenAPI, público" GET /openapi.json
confere 401 "sem token" GET /vehicle/
confere 401 "senha errada" POST /auth/token "" '{"name":"smoke-usuario","password":"errada"}'

for papel in usuario motorista admin silver; do
  confere 200 "login de $papel" POST /auth/token "" \
    "{\"name\":\"smoke-$papel\",\"password\":\"$senha\"}"
  printf -v "token_$papel" '%s' "$(campo access_token)"
done

confere 200 "usuario: quem sou eu" GET /auth/me "$token_usuario"

# Cadastro público: nasce usuario, e o papel nem faz parte do contrato.
novo="{\"name\":\"smoke-novo\",\"password\":\"$senha\"}"
confere 201 "cadastro público" POST /auth/register "" "$novo"
confere 409 "cadastro repetido" POST /auth/register "" "$novo"
confere 422 "cadastro não escolhe papel" POST /auth/register "" \
  "{\"name\":\"smoke-outro\",\"password\":\"$senha\",\"role\":\"admin\"}"

# Sessão: cada token de renovação vale uma troca, e o reuso derruba a família.
confere 200 "login da sessão" POST /auth/token "" "$novo"
renovacao_1="$(campo refresh_token)"
confere 401 "renovação não abre rota" GET /auth/me "$renovacao_1"
confere 200 "renova" POST /auth/refresh "$renovacao_1"
renovacao_2="$(campo refresh_token)"
confere 401 "reuso do token trocado" POST /auth/refresh "$renovacao_1"
confere 401 "reuso derruba o sucessor" POST /auth/refresh "$renovacao_2"
confere 200 "login de outra sessão" POST /auth/token "" "$novo"
renovacao_3="$(campo refresh_token)"
confere 204 "logout" POST /auth/logout "$renovacao_3"
confere 401 "renova depois do logout" POST /auth/refresh "$renovacao_3"

# Veículo: o VIN é único entre todos os donos (e vale a forma canônica), a silver
# não mexe em veículo e só o admin tem escopo sobre o alheio.
vin="93YBSR7RHEJ298116"
confere 201 "usuario cadastra veículo" POST /vehicle/ "$token_usuario" "{\"vin\":\"$vin\"}"
veiculo="$(campo id)"
veiculo="${veiculo:-0}"
confere 409 "VIN de outro gestor" POST /vehicle/ "$token_motorista" '{"vin":"93ybsr7rhej298116"}'
confere 422 "VIN inválido" POST /vehicle/ "$token_usuario" '{"vin":"93YBSR7RHEJ29811O"}'
confere 200 "usuario lista os veículos" GET /vehicle/ "$token_usuario"
confere 403 "silver não cadastra" POST /vehicle/ "$token_silver" '{"vin":"9BWZZZ377VT004251"}'
confere 403 "silver não altera" PATCH "/vehicle/$veiculo" "$token_silver" '{"nickname":"x"}'
confere 403 "silver não exclui" DELETE "/vehicle/$veiculo" "$token_silver"
confere 404 "outro não vê o veículo" GET "/vehicle/$veiculo" "$token_motorista"
confere 404 "outro não altera o veículo" PATCH "/vehicle/$veiculo" "$token_motorista" \
  '{"nickname":"x"}'
confere 200 "admin altera o veículo" PATCH "/vehicle/$veiculo" "$token_admin" '{"nickname":"smoke"}'

# Motoristas: o gestor adiciona pelo login, o motorista não vê os outros motoristas
# e a silver não mexe em nada.
motoristas="/vehicle/$veiculo/drivers"
confere 201 "gestor adiciona motorista" POST "$motoristas" "$token_usuario" \
  '{"user_name":"smoke-motorista"}'
motorista="$(campo driver_id)"
motorista="${motorista:-0}"
confere 409 "motorista repetido" POST "$motoristas" "$token_usuario" '{"user_name":"smoke-motorista"}'
confere 404 "login inexistente" POST "$motoristas" "$token_usuario" '{"user_name":"ninguem"}'
confere 422 "gestor como motorista" POST "$motoristas" "$token_usuario" \
  '{"user_name":"smoke-usuario"}'
confere 403 "silver não adiciona" POST "$motoristas" "$token_silver" '{"user_name":"smoke-admin"}'
confere 404 "motorista não vê os outros" GET "$motoristas" "$token_motorista"
confere 200 "motorista lista o que dirige" GET /vehicle/driving "$token_motorista"

# Telemetria: o motorista envia um lote aceito em parte (o VIN que ele dirige vai
# para a fila; o alheio volta no recibo), a silver não envia, o formato inválido
# reprova o lote inteiro, e o motorista removido não envia mais nada.
leitura='"collected_at":"2026-11-07T12:00:00Z","readings":[{"metric":"rpm","unit":"rpm","available":true,"value":800.0}]'
lote="{\"records\":[{\"id\":\"00000000-0000-4000-8000-00000000000a\",\"vin\":\"$vin\",$leitura},"
lote+="{\"id\":\"00000000-0000-4000-8000-00000000000b\",\"vin\":\"9BWZZZ377VT004251\",$leitura}]}"
if confere 202 "motorista envia em parte" POST /telemetry/batches "$token_motorista" "$lote"; then
  total=$((total + 1))
  if [ "$(campo accepted)" = "1" ] && grep -q '"vehicle_not_authorized"' "$tmp/corpo"; then
    echo "OK      recibo: 1 aceito, o VIN alheio em rejected"
  else
    falhas=$((falhas + 1))
    echo "FALHOU  recibo: $(head -c 300 "$tmp/corpo")"
  fi
fi
confere 403 "silver não envia" POST /telemetry/batches "$token_silver" "$lote"
confere 422 "lote vazio" POST /telemetry/batches "$token_usuario" '{"records":[]}'
# Acima de 1 MiB (MAX_CONTENT_LENGTH): o printf da requisicao é builtin, sem limite de argumento.
corpo_grande="{\"records\":\"$(head -c 1100000 /dev/zero | tr '\0' 'x')\"}"
confere 413 "corpo acima de 1 MiB" POST /telemetry/batches "$token_usuario" "$corpo_grande"
confere 200 "gestor remove o motorista" DELETE "$motoristas/$motorista" "$token_usuario"
if confere 202 "motorista removido envia" POST /telemetry/batches "$token_motorista" "$lote"; then
  total=$((total + 1))
  if [ "$(campo accepted)" = "0" ]; then
    echo "OK      recibo: nada aceito do motorista removido"
  else
    falhas=$((falhas + 1))
    echo "FALHOU  recibo do motorista removido: $(head -c 300 "$tmp/corpo")"
  fi
fi

# O 202 só sai depois da confirmação do broker: a mensagem já está na fila (uma
# só: o envio do motorista removido não publicou nada). A
# contagem que o rabbitmqctl lê de uma fila quorum é atualizada a cada poucos
# segundos, e por isso a espera.
total=$((total + 1))
for _ in $(seq 1 15); do
  na_fila="$(rabbitmqctl list_queues -p "$vhost" --no-table-headers name messages 2>/dev/null |
    tr -d '\r' | awk '$1 == "bronze.telemetry" { print $2 }')"
  [ "$na_fila" = "1" ] && break
  sleep 1
done
if [ "$na_fila" = "1" ]; then
  echo "OK      fila bronze.telemetry do vhost $vhost com 1 mensagem"
else
  falhas=$((falhas + 1))
  echo "FALHOU  fila bronze.telemetry: esperava 1 mensagem, encontrei '${na_fila:-nada}'"
fi
confere 200 "gestor exclui veículo" DELETE "/vehicle/$veiculo" "$token_usuario"
confere 404 "veículo excluído some" GET "/vehicle/$veiculo" "$token_usuario"

# A última resposta é um 404: o id precisa voltar no header e no envelope de erro.
total=$((total + 1))
eco_header="$(tr -d '\r' <"$tmp/headers" | sed -n 's/^[Xx]-[Rr]equest-[Ii][Dd]: *//p' | tail -n 1)"
eco_corpo="$(campo request_id)"
if [ "$eco_header" = "$rid" ] && [ "$eco_corpo" = "$rid" ]; then
  echo "OK      X-Request-ID devolvido no header e no envelope de erro"
else
  falhas=$((falhas + 1))
  echo "FALHOU  X-Request-ID: enviado $rid; header '$eco_header'; envelope '$eco_corpo'"
fi

docker logs "$container" >"$tmp/app.log" 2>&1
total=$((total + 1))
no_log="$(grep -E "\"request_id\": \"smoke-$execucao-[0-9]+\"" "$tmp/app.log" | grep -c '"event": "http.access"')"
if [ "$no_log" -eq "$n" ]; then
  echo "OK      log de acesso: as $n requisições aparecem em JSON com o seu request_id"
else
  falhas=$((falhas + 1))
  echo "FALHOU  log de acesso: $no_log de $n requisições com o seu request_id"
fi

# Fora do `requisicao` de propósito: o /metrics não entra na contagem do log de acesso.
total=$((total + 1))
curl -s --max-time 10 "http://127.0.0.1:$porta/metrics" >"$tmp/metrics"
contadas="$(awk '/^http_requests_total\{/ { soma += $NF } END { printf "%d", soma }' "$tmp/metrics")"
if [ "$contadas" -ge "$n" ] && grep -q 'route="/api/v1/vehicle/<int:vehicle_id>"' "$tmp/metrics"; then
  echo "OK      /metrics soma os workers ($contadas requisições) e rotula pelo molde da rota"
else
  falhas=$((falhas + 1))
  echo "FALHOU  /metrics: $contadas requisições contadas de pelo menos $n, ou sem o molde da rota"
fi

if [ "${#ids_com_falha[@]}" -gt 0 ]; then
  echo ">> linhas do log das requisições que falharam:"
  for r in "${ids_com_falha[@]}"; do
    grep -F "\"request_id\": \"$r\"" "$tmp/app.log" | sed 's/^/        /'
  done
  if [ "$erro_servidor" -eq 1 ]; then
    echo ">> houve 5xx ou requisição sem resposta; fim do log do container:"
    tail -n 40 "$tmp/app.log" | sed 's/^/        /'
  fi
fi

if [ "$falhas" -gt 0 ]; then
  echo "RESULTADO: FALHOU — $falhas de $total verificações"
  exit 1
fi
echo "RESULTADO: OK — $total verificações"
