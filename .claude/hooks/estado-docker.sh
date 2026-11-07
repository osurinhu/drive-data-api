#!/usr/bin/env bash
# Hook SessionStart (registrado em .claude/settings.json): resume em uma linha o
# estado do Docker, do qual dependem /verificar, /migracao e /smoke-api.
#
# O stdout entra no contexto da sessão: a saída é curta e o script sempre sai
# com 0 — um hook que falhasse só faria a sessão começar sem esta linha.
# `docker version`, e não `docker info`: responde a mesma pergunta (o daemon
# responde?) em ~0,5 s, contra ~2 s (e 6 s a frio) do `docker info`.
# Nada aqui lê o .env: se o compose não carregar, a mensagem só aponta o comando
# que mostra o motivo.

set -u

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" 2>/dev/null || exit 0

# GNU timeout existe no Git Bash e no Linux; no macOS pode faltar, e então vale
# só o timeout do próprio hook.
limite() {
  if command -v timeout >/dev/null 2>&1; then timeout 10 "$@"; else "$@"; fi
}

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker: CLI não encontrado — /verificar, /migracao e /smoke-api dependem dele."
  exit 0
fi

versao="$(limite docker version --format '{{.Server.Version}}' 2>/dev/null)" || versao=""
if [ -z "$versao" ]; then
  echo "Docker: o daemon não respondeu — /verificar, /migracao e /smoke-api falham até" \
    "o Docker Desktop estar no ar (peça ao usuário para abri-lo)."
  exit 0
fi

if ! estado="$(limite docker compose ps --all \
  --format '{{.Name}} {{.Service}} {{.State}} {{.Health}}' 2>/dev/null)"; then
  echo "Docker $versao no ar, mas o docker compose não carregou o projeto (falta o .env?)" \
    "— 'docker compose config -q' mostra o motivo."
  exit 0
fi

# `--all` também lista containers avulsos (`docker compose run`) do mesmo serviço:
# o do serviço em si é o que termina em -<n> (base-api-api-1).
printf '%s\n' "$estado" | awk -v versao="$versao" '
  NF >= 3 {
    s = $3
    if ($4 != "") s = s " (" $4 ")"
    if (!($2 in estado) || $1 ~ /-[0-9]+$/) estado[$2] = s
  }
  END {
    db = ("db" in estado) ? estado["db"] : "sem container"
    api = ("api" in estado) ? estado["api"] : "sem container"
    printf "Docker %s no ar — db: %s; api: %s.\n", versao, db, api
  }'
exit 0
