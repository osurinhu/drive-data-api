---
paths:
  - "Dockerfile"
  - "docker-compose.yml"
  - "docker/**"
  - ".github/workflows/**"
  - "requirements*.txt"
  - "pyproject.toml"
  - ".dockerignore"
  - ".gitattributes"
  - ".vscode/settings.json"
---

# Infraestrutura, dependências e CI

Decisões em 02-decisoes, seções Operação e Qualidade; o runbook é o `README.md`.

- Os serviços que rodam a imagem da aplicação (`setup`, `api`, `bronze-api`, `consumer`) herdam o
  `x-runtime` do compose, que fixa `target: runtime`: sem ele o Docker constrói o último estágio
  (`dev`) e a aplicação sobe rodando pytest.
- Quem migra no compose é o job `setup` (`docker/setup.sh`). Processo novo do compose leva
  `<<: *runtime`, `FLASK_PROCESS`, `RUN_MIGRATIONS: "0"` e `depends_on` do `setup` com
  `service_completed_successfully`. O padrão do entrypoint continua `RUN_MIGRATIONS=1`, para o
  container avulso (o do `/smoke-api`) migrar sozinho. O `setup` também aplica a topologia
  (`flask rabbitmq setup`) e os índices do bronze (`flask mongo setup`), e espera o broker e o
  Mongo saudáveis.
- O `consumer` não publica porta (escala em réplicas). O healthcheck do compose roda dentro do
  container, em `:8081/health/ready`. Sem o broker ou o Mongo ele fica `unhealthy`, de pé, e volta
  sozinho. Ele trata SIGTERM (é o PID 1): o `docker compose stop consumer` termina a mensagem
  corrente e devolve as outras à fila.
- RabbitMQ: o `hostname` é fixo porque o nó (`rabbit@<hostname>`) é a chave dos dados no volume.
  O healthcheck roda como `gosu rabbitmq ...`, no compose e no CI. Como root, o CLI cria um
  `.erlang.cookie` do root na primeira subida do volume, e o servidor cai com `eacces`.
- Healthcheck que sobe uma CLI pesada (`mongosh`, `rabbitmq-diagnostics`) usa `timeout` de 20 s,
  no compose e no CI. Com 5 s, ele estourava durante um build, e o `test` recusava subir com a
  dependência `unhealthy`. No compose, também `interval` de 30 s e `start_interval` curto.
- As senhas do `.env` entram nas URLs que o compose monta: só `token_urlsafe` (letras, números,
  `-` e `_`). Postgres, Mongo e RabbitMQ criam o usuário só na primeira subida do volume.
- O estágio `runtime` usa `COPY` explícito (só o que roda); o `dev` acrescenta testes e
  ferramentas. `tests/` não pode entrar no `.dockerignore`.
- Dependência nova: edite `requirements.txt` (faixa de versão) e regenere `requirements.lock.txt`
  com o comando do cabeçalho dele — o Docker instala o lock. No Git Bash, troque `${PWD}` por
  `$(pwd -W)` e prefixe `MSYS_NO_PATHCONV=1`, senão o `-w /srv` vira caminho do Windows.
- O fato de `app/` ser a raiz do `sys.path` está codificado em cinco lugares: `pythonpath` e `src`
  no `pyproject.toml`, `PYTHONPATH` no `Dockerfile`, `sys.path.insert` em `migrations/env.py` e
  `python.analysis.extraPaths` em `.vscode/settings.json`. Mudar o layout exige mudar os cinco.
- O CI (`.github/workflows/ci.yml`) e `.claude/skills/verificar/verificar.sh` executam as mesmas
  etapas: mudou um, mude o outro.
- Workers vêm de `WEB_CONCURRENCY`; ao mudar, refaça a conta
  `workers × (pool_size + max_overflow) ≤ max_connections`. Nada de `--preload` sem descartar o
  engine no `post_fork`.
- Scripts `.sh` ficam em LF (`.gitattributes`): com CRLF, o `entrypoint.sh` derruba o container.
- As portas do `db` (5432), do `mongo` (27017) e do `rabbitmq` (5672 e 15672), o bind mount de
  `./migrations` no `api` e o limite de cache do Mongo são conveniências de desenvolvimento e
  saem antes de produção (README, "Antes de ir para produção").
