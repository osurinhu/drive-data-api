#!/usr/bin/env bash
# Portão de qualidade local: as mesmas etapas do CI (.github/workflows/ci.yml),
# executadas nos containers do docker-compose.
#
# Por que assim:
#   * O host não tem pytest/ruff/mypy/alembic; a imagem `dev` (serviço `test`) tem.
#   * O código é COPIADO para a imagem: sem rebuild, testa-se a versão antiga.
#     Por isso todo modo reconstrói antes (com cache, custa poucos segundos).
#   * As etapas rodam num único container, que recebe o roteiro por stdin — nada
#     de aspas atravessando a linha de comando do Windows.
#   * O banco descartável é criado pelo próprio container `db`, e a URI dele é
#     derivada de FLASK_TEST_DATABASE_URI dentro do container: o .env não é lido.

set -uo pipefail

# Git Bash (Windows): sem isto, argumentos como `/srv` viram `C:/Program Files/Git/srv`.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

uso() {
  cat <<'USO'
Uso (a partir da raiz do repositório):
  bash .claude/skills/verificar/verificar.sh [modo] [argumentos do pytest]

Modos:
  tudo (padrão)   ruff check, ruff format --check, mypy, migrações e pytest
  estatico        ruff check, ruff format --check e mypy (não precisa de banco)
  testes [args]   só o pytest; os argumentos são repassados
                  ex.: testes tests/test_vehicle.py -k vin
  migracoes       upgrade head -> check -> downgrade base num banco descartável
  medir [args]    roda a medição temporária tests/test_zz_medir.py e mostra a
                  saída inteira, mesmo quando passa (ver /medir-consultas)

Saída: 0 = tudo OK; 1 = alguma etapa falhou;
       2 = infraestrutura (Docker parado, build quebrado, banco fora do ar).
USO
}

raiz="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "INFRA: rode dentro do repositório." >&2
  exit 2
}
cd "$raiz" || exit 2

modo="${1:-tudo}"
[ $# -gt 0 ] && shift

case "$modo" in
  tudo) etapas="estatico,migracoes,testes" ;;
  estatico | testes | migracoes | medir) etapas="$modo" ;;
  -h | --help | ajuda) uso; exit 0 ;;
  *) echo "Modo desconhecido: $modo" >&2; uso >&2; exit 2 ;;
esac

if [ "$modo" != "testes" ] && [ "$modo" != "medir" ] && [ $# -gt 0 ]; then
  echo "Argumentos extras só valem nos modos 'testes' e 'medir'." >&2
  exit 2
fi

# A medição do /medir-consultas é temporária: o modo `medir` precisa dela, e o
# `tudo` (o portão antes de concluir) recusa rodar com ela esquecida em tests/.
medicao="tests/test_zz_medir.py"
if [ "$modo" = "medir" ] && [ ! -f "$medicao" ]; then
  echo "Modo medir: crie $medicao antes (modelo no SKILL.md do /medir-consultas)." >&2
  exit 2
fi
if [ "$modo" = "tudo" ] && [ -f "$medicao" ]; then
  echo "FALHOU  $medicao é a medição temporária do /medir-consultas: apague-a antes de concluir."
  echo "RESULTADO: FALHOU — medição temporária esquecida em tests/"
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

falha_infra() {
  echo "INFRA: $1" >&2
  if [ -n "${2:-}" ] && [ -s "$2" ]; then tail -n 40 "$2" >&2; fi
  exit 2
}

# `docker version`, e não `docker info`: a mesma pergunta (o daemon responde?)
# em ~0,5 s, contra ~2 s (e 6 s a frio) do `docker info`.
docker version --format '{{.Server.Version}}' >/dev/null 2>&1 ||
  falha_infra "Docker indisponível — abra o Docker Desktop e rode de novo."

echo ">> build da imagem de teste com o código atual..."
docker compose build -q test >"$tmp/build.log" 2>&1 ||
  falha_infra "o build da imagem de teste falhou." "$tmp/build.log"

banco_mig=0
case ",$etapas," in
  *,migracoes,*)
    docker compose up -d --wait db >"$tmp/db.log" 2>&1 ||
      falha_infra "o banco não subiu." "$tmp/db.log"
    docker compose exec -T db sh -s >"$tmp/mig.log" 2>&1 <<'CRIAR_BANCO' ||
psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}_mig\" WITH (FORCE)" \
  -c "CREATE DATABASE \"${POSTGRES_DB}_mig\" OWNER \"$POSTGRES_USER\""
CRIAR_BANCO
      falha_infra "não consegui criar o banco descartável." "$tmp/mig.log"
    banco_mig=1
    ;;
esac

# Só o estático dispensa os serviços; os outros modos sobem o `db`, o `mongo` e o
# `rabbitmq` como dependências do `test` (e esperam os três saudáveis).
sem_banco=()
[ "$etapas" = "estatico" ] && sem_banco=(--no-deps)

# Roteiro executado dentro do container `test` (cwd /srv, Python 3.12).
# $1 = etapas separadas por vírgula; o resto são argumentos do pytest.
docker compose run --rm -T ${sem_banco[@]+"${sem_banco[@]}"} test sh -s -- "$etapas" "$@" \
  <<'ROTEIRO' 2>"$tmp/compose.log" | tee "$tmp/saida.log"
etapas="$1"
shift
falhas=""

etapa() {
  nome="$1"
  shift
  saida="$(mktemp)"
  inicio="$(date +%s)"
  # </dev/null: o roteiro chega por stdin; uma etapa que lesse stdin o engoliria.
  if "$@" </dev/null >"$saida" 2>&1; then
    ultima="$(grep -v '^[[:space:]]*$' "$saida" | tail -n 1)"
    echo "OK      $nome ($(( $(date +%s) - inicio ))s)${ultima:+ — $ultima}"
  else
    echo "FALHOU  $nome ($(( $(date +%s) - inicio ))s)"
    tail -n 80 "$saida" | sed 's/^/        /'
    falhas="$falhas, $nome"
  fi
  rm -f "$saida"
}

formatacao() {
  ruff format --check . >/dev/null 2>&1 && return 0
  ruff format --diff . 2>&1
  return 1
}

# Subshell (parênteses): a URI do banco descartável não vaza para as etapas seguintes.
migracoes() (
  FLASK_SQLALCHEMY_DATABASE_URI="${FLASK_TEST_DATABASE_URI%_test}_mig"
  export FLASK_SQLALCHEMY_DATABASE_URI
  alembic upgrade head && alembic check && alembic downgrade base &&
    echo "upgrade head -> check -> downgrade base sem divergência"
)

for e in $(echo "$etapas" | tr ',' ' '); do
  case "$e" in
    estatico)
      etapa "ruff check" ruff check .
      etapa "ruff format --check" formatacao
      etapa "mypy" mypy
      ;;
    migracoes)
      etapa "alembic" migracoes
      ;;
    testes)
      etapa "pytest" pytest -q --tb=short "$@"
      ;;
    medir)
      # O relatório é o resultado: a saída sai inteira, mesmo quando passa.
      inicio="$(date +%s)"
      if pytest -q -s --tb=short tests/test_zz_medir.py "$@" </dev/null 2>&1; then
        echo "OK      medição ($(( $(date +%s) - inicio ))s)"
      else
        echo "FALHOU  medição"
        falhas="$falhas, medição"
      fi
      ;;
  esac
done

if [ -n "$falhas" ]; then
  echo "RESULTADO: FALHOU — ${falhas#, }"
  exit 1
fi
echo "RESULTADO: OK"
ROTEIRO
rc=${PIPESTATUS[0]}

if [ "$banco_mig" -eq 1 ]; then
  docker compose exec -T db sh -s >/dev/null 2>&1 <<'APAGAR_BANCO' ||
psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}_mig\" WITH (FORCE)"
APAGAR_BANCO
    echo "aviso: não consegui apagar o banco descartável <POSTGRES_DB>_mig." >&2
fi

# Sem a linha RESULTADO, quem falhou foi o compose ou o container, não o código.
if ! grep -q '^RESULTADO:' "$tmp/saida.log"; then
  echo "INFRA: o container de teste não completou o roteiro (código $rc)." >&2
  tail -n 40 "$tmp/compose.log" >&2
  exit 2
fi
exit "$rc"
