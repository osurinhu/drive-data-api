#!/usr/bin/env bash
# Smoke da API real: o código atual, na imagem de produção (entrypoint com
# migrações + gunicorn), conferido por HTTP de ponta a ponta.
#
# Por que assim:
#   * Container avulso (`docker compose run api`) num banco descartável: os dados
#     do banco de desenvolvimento e o `api` que já está rodando ficam intactos.
#   * O banco descartável entra pela própria interpolação do compose
#     (POSTGRES_DB=<nome>_smoke): o .env não é lido e nenhum segredo passa por aqui.
#   * As migrações rodam do zero, pelo entrypoint de verdade: se a aplicação não
#     sobe num banco vazio, o smoke falha antes da primeira requisição.
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
confere por HTTP: health, OpenAPI, login, CRUD de item, a matriz 401/403/404/409/422
e o X-Request-ID no header, no envelope de erro e no log de acesso.

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
tmp="$(mktemp -d)"
# curl é programa nativo no Windows: os arquivos dele precisam de caminho C:/...
if command -v cygpath >/dev/null 2>&1; then tmp="$(cygpath -m "$tmp")"; fi
banco=""

limpar() {
  docker rm -f "$container" >/dev/null 2>&1
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

echo ">> build da imagem do api com o código atual..."
docker compose build -q api >"$tmp/build.log" 2>&1 ||
  falha_infra "o build da imagem do api falhou." "$tmp/build.log"

docker compose up -d --wait db >"$tmp/db.log" 2>&1 ||
  falha_infra "o banco não subiu." "$tmp/db.log"

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
POSTGRES_DB="$banco" docker compose run -d -T --no-deps --name "$container" \
  --publish 127.0.0.1::5000 api >"$tmp/run.log" 2>&1 ||
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
  for papel in leitor usuario gerente admin; do
    flask create-user --name "smoke-$papel" --password "$SMOKE_SENHA" --role "$papel" || exit 1
  done' >"$tmp/usuarios.log" 2>&1; then
  echo "FALHOU  flask create-user:"
  tail -n 20 "$tmp/usuarios.log" | sed 's/^/        /'
  echo "RESULTADO: FALHOU — flask create-user"
  exit 1
fi
echo ">> flask create-user: smoke-leitor, smoke-usuario, smoke-gerente e smoke-admin"

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
confere 200 "spec OpenAPI, público" GET /openapi.json
confere 401 "sem token" GET /item/
confere 401 "senha errada" POST /auth/token "" '{"name":"smoke-usuario","password":"errada"}'

for papel in leitor usuario gerente admin; do
  confere 200 "login de $papel" POST /auth/token "" \
    "{\"name\":\"smoke-$papel\",\"password\":\"$senha\"}"
  printf -v "token_$papel" '%s' "$(campo access_token)"
done

confere 200 "usuario: quem sou eu" GET /auth/me "$token_usuario"
confere 201 "usuario cria" POST /item/ "$token_usuario" '{"name":"smoke"}'
item="$(campo id)"
item="${item:-0}"
confere 409 "nome repetido" POST /item/ "$token_usuario" '{"name":"smoke"}'
confere 422 "corpo inválido" POST /item/ "$token_usuario" '{"campo":"x"}'
confere 200 "usuario lista" GET /item/ "$token_usuario"
confere 403 "leitor não cria" POST /item/ "$token_leitor" '{"name":"do leitor"}'
confere 403 "leitor não altera" PATCH "/item/$item" "$token_leitor" '{"name":"x"}'
confere 403 "leitor não exclui" DELETE "/item/$item" "$token_leitor"
confere 404 "leitor não vê o alheio" GET "/item/$item" "$token_leitor"
confere 200 "gerente lê o alheio" GET "/item/$item" "$token_gerente"
confere 404 "gerente não altera o alheio" PATCH "/item/$item" "$token_gerente" '{"name":"x"}'
confere 200 "admin altera o alheio" PATCH "/item/$item" "$token_admin" '{"name":"smoke 2"}'
confere 200 "dono exclui (lógica)" DELETE "/item/$item" "$token_usuario"
confere 404 "excluído some" GET "/item/$item" "$token_usuario"

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
no_log="$(grep -cE "req=smoke-$execucao-[0-9]+$" "$tmp/app.log")"
if [ "$no_log" -eq "$n" ]; then
  echo "OK      log de acesso: as $n requisições aparecem com o seu req="
else
  falhas=$((falhas + 1))
  echo "FALHOU  log de acesso: $no_log de $n requisições com o seu req="
fi

if [ "${#ids_com_falha[@]}" -gt 0 ]; then
  echo ">> linhas do log das requisições que falharam:"
  for r in "${ids_com_falha[@]}"; do
    grep -F "req=$r" "$tmp/app.log" | sed 's/^/        /'
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
