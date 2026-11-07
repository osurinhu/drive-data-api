# Base API

Projeto base para APIs REST em Flask: arquitetura em camadas, injeção de dependência,
autenticação JWT, migrações, observabilidade, testes e containerização já montados e
funcionando.

**Este arquivo é o runbook** — como subir, testar, migrar e configurar.
Para entender e estender o projeto, veja **[documentacao/](documentacao/)**:

| | |
|---|---|
| [Arquitetura](documentacao/01-arquitetura.md) | Camadas, fluxo de uma requisição, e o que cada camada pode e não pode fazer |
| [Decisões](documentacao/02-decisoes.md) | Por que cada escolha foi feita, e o que foi descartado |
| [Como estender](documentacao/03-como-estender.md) | Passo a passo para cada tipo de mudança |

---

## Stack

| Camada | Escolha |
|---|---|
| Linguagem | Python 3.12 |
| Framework web | Flask 3.1 |
| API + documentação | flask-smorest (OpenAPI 3 e Swagger UI automáticos) |
| ORM | Flask-SQLAlchemy 3.1 / SQLAlchemy 2.x |
| Banco | PostgreSQL 16 (driver `psycopg` 3) |
| Camada bronze | MongoDB 8.0 (cliente `pymongo`) |
| Fila | RabbitMQ 4.4 (cliente `pika`) |
| Migrações | Alembic |
| Autenticação | Flask-JWT-Extended |
| Injeção de dependência | `dependency-injector` |
| Configuração | Dynaconf (`settings.toml` + variáveis de ambiente) |
| Testes | pytest (contra PostgreSQL, MongoDB e RabbitMQ reais) |
| Lint / formatação | ruff |
| Tipos | mypy (camadas de lógica pura) |
| Execução | Gunicorn em Docker, orquestrado por docker-compose |

---

## Começando

Pré-requisitos: Docker e Docker Compose.

```bash
cp .env.example .env
# edite o .env: troque POSTGRES_PASSWORD, MONGO_ROOT_PASSWORD, RABBITMQ_ADMIN_PASSWORD,
# SECRET_KEY e JWT_SECRET_KEY
# gere cada segredo com (use 32 bytes ou mais):
#   python -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose up --build
```

Isso sobe o PostgreSQL (e cria o banco de testes), o MongoDB e o RabbitMQ. Depois roda o job
`setup`, que aplica as migrações e a topologia do RabbitMQ (exchange `telemetry` e fila
`bronze.telemetry`) e termina, e só então inicia os dois processos HTTP: o `api`
(porta 5000) e o `bronze-api` (porta 5001). O compose falha com mensagem explícita se algum
segredo do `.env` estiver faltando — nunca sobe com segredo vazio.

As senhas entram nas URLs de conexão que o compose monta: use só letras, números, `-` e `_`
(o `token_urlsafe` já gera assim). As do Postgres, do Mongo e do RabbitMQ valem só na
primeira subida de cada volume, que é quando o usuário é criado; para trocá-las depois, apague
o volume do serviço com `docker volume rm` (ver `.env.example`).

Crie o primeiro usuário e experimente:

```bash
docker compose exec api flask create-user --name admin --password sua-senha-aqui --role admin
```

Os papéis são `leitor` (só leitura), `usuario`, `gerente`, `admin` e `silver` (a conta de
sistema da camada silver: só lê o bronze, e só a CLI a cria); o padrão é `usuario`.
Para trocar o papel de alguém depois: `flask set-role --name admin --role gerente` (vale na
requisição seguinte, mesmo com o token já emitido).

- **Swagger UI:** http://localhost:5000/api/v1/ (e http://localhost:5001/api/v1/, o `bronze-api`)
- **Especificação OpenAPI:** http://localhost:5000/api/v1/openapi.json
- **Health:** http://localhost:5000/api/v1/health/ (uma chave por dependência do processo) e
  `/api/v1/health/live` (só o processo)
- **Painel do RabbitMQ:** http://localhost:15672, com `RABBITMQ_ADMIN_USER` e a senha do `.env`

| Processo | `FLASK_PROCESS` | Porta | Dependências | Sem ela, o `/health/` responde |
|---|---|---|---|---|
| `api` | `api` | 5000 | Postgres, RabbitMQ | 503 sem o Postgres; 200 `degraded` sem o RabbitMQ |
| `bronze-api` | `bronze_api` | 5001 | Postgres, Mongo | 503 sem qualquer um dos dois |
| `setup` (job) | `setup` | — | Postgres, RabbitMQ (API de gerenciamento) | termina depois de migrar e aplicar a topologia |

```bash
# obter um token
curl -X POST http://localhost:5000/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"name":"admin","password":"sua-senha-aqui"}'

# usar o token
curl http://localhost:5000/api/v1/item/ -H "Authorization: Bearer <token>"

# cadastrar um veículo e enviar um lote de leituras dele (202: o lote está na fila)
curl -X POST http://localhost:5000/api/v1/vehicle/ -H "Authorization: Bearer <token>" \
  -H 'Content-Type: application/json' -d '{"vin":"93YBSR7RHEJ298116"}'
curl -X POST http://localhost:5000/api/v1/telemetry/batches -H "Authorization: Bearer <token>" \
  -H 'Content-Type: application/json' -d '{"records":[{"id":"9f1c2a7e-6b4d-4d0e-9a51-2f6f1b0c8e11",
  "vin":"93YBSR7RHEJ298116","collected_at":"2026-11-02T14:03:11.482Z",
  "readings":[{"metric":"rpm","unit":"rpm","available":true,"value":1065.0}]}]}'
```

O lote é aceito em parte: o registro de um veículo de que você não é o gestor volta em
`rejected`, e os outros seguem para a fila. Com o RabbitMQ fora, a resposta é 503 com
`Retry-After`, e o lote pode ser reenviado inteiro (cada registro é gravado uma vez só, pelo
`id`). Por enquanto a mensagem fica na fila `bronze.telemetry`, visível no painel: o consumer
que a grava no bronze é o passo seguinte.

### Rodando fora do Docker

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
# no .env, descomente FLASK_SQLALCHEMY_DATABASE_URI, FLASK_MONGO_URI, FLASK_AMQP_URL e
# FLASK_AMQP_MANAGEMENT_URL (apontam para localhost) e suba as dependências:
# docker compose up -d db mongo rabbitmq
alembic upgrade head                          # o banco, na raiz do repositório
cd app && flask rabbitmq setup && flask run   # a topologia da fila, depois a API
```

Sem `FLASK_PROCESS`, vale o perfil `[default]`, que carrega tudo e por isso pede as três URIs.
Com `FLASK_PROCESS=api` no `.env`, a aplicação sobe como o processo `api` e nem pede o Mongo.

---

## Estrutura

```
app/
  app.py                  create_app(): factory; não conhece nenhuma extensão
  instrumentation/        observação usada por mais de uma extensão (sondas do health, id da requisição)
  messaging/              RabbitMQ: conexão, sonda, API de gerenciamento, topologia e publisher
  ext/                    infraestrutura — cada módulo expõe init_app(app)
  domain/                 modelos ORM e o vocabulário puro (papéis e permissões, VIN, portas, contrato da fila)
  repository/             acesso a dados, uma subpasta por tecnologia (sql/: o único lugar que monta SQL)
  service/                regra de negócio, validação, ownership; telemetry/: o pipeline, só com portas
  schemas/                contrato de entrada/saída (marshmallow)
  blueprint/              camada HTTP: envelope de erro e, em restapi/v1/, as rotas (uma por recurso)
migrations/               Alembic
tests/                    pytest: a raiz usa Postgres; unit/, mongo/, rabbitmq/ e e2e/ (os três), o que o nome diz
docker/                   entrypoint, setup.sh (o job de preparação) e, por serviço, os scripts de init
settings.toml             configuração (sem segredos), registro de módulos e um perfil por processo
requirements.lock.txt     versões exatas do build (versionado)
documentacao/             arquitetura, decisões e guias de extensão
```

Detalhamento de cada camada e das regras de dependência:
**[documentacao/01-arquitetura.md](documentacao/01-arquitetura.md)**.

---

## Testes

A suíte roda contra **PostgreSQL, MongoDB e RabbitMQ de verdade**, não contra imitações. O
banco `<POSTGRES_DB>_test` é criado automaticamente na primeira subida do compose. No Mongo a
suíte usa o banco `<MONGO_DATABASE>_test`, e no RabbitMQ o vhost `drive-data-test`, que ela
mesma cria. Os dados de desenvolvimento ficam intactos.

```bash
# no container (não precisa de Python local)
docker compose run --rm test

# ou localmente, contra os serviços do compose
docker compose up -d db mongo rabbitmq
pytest
pytest tests/unit     # só os testes que não usam serviço nenhum
```

As pastas de `tests/` dizem a infraestrutura que cada teste exige. A raiz usa Postgres e HTTP,
`unit/` não usa nada, `mongo/` e `rabbitmq/` usam o serviço do nome, e `e2e/` usa os três. Cada
pasta tem o próprio `conftest.py`. As tabelas são esvaziadas depois de cada teste, assim como as coleções do banco de
testes do Mongo, então a ordem de execução não importa. Os nomes de arquivo de teste não se
repetem entre as pastas: sem `__init__.py`, o pytest recusa dois arquivos com o mesmo nome.

```bash
ruff check .
ruff format .
mypy
```

O CI (`.github/workflows/ci.yml`) roda tudo isso em cada push, com PostgreSQL, MongoDB e
RabbitMQ de serviço, e inclui `alembic check` — que reprova se as migrações divergirem dos
modelos.

---

## Migrações

```bash
# criar uma migração a partir das mudanças nos modelos
docker compose exec api alembic revision --autogenerate -m "descrição"

# aplicar (o job `setup` já faz isso a cada `docker compose up`)
docker compose exec api alembic upgrade head

# voltar uma versão
docker compose exec api alembic downgrade -1

# conferir se modelos e migrações estão em sincronia
docker compose exec api alembic check
```

O `migrations/env.py` lê a URL do banco da configuração da aplicação — a string de conexão
não é duplicada no `alembic.ini`. Ele carrega **apenas** a configuração, não a aplicação
inteira: migrar o banco não deve exigir que segredos de JWT e CORS estejam definidos.

Quem migra no compose é o job `setup` (`docker/setup.sh`): ele roda antes dos processos, que
sobem com `RUN_MIGRATIONS=0` e só depois que ele termina com sucesso. Com dois processos (e
réplicas) migrando na subida, os dois disputariam o mesmo banco vazio. Fora do compose, o
entrypoint da imagem continua migrando na subida (`RUN_MIGRATIONS=1` é o padrão), como faz o
container avulso do `/smoke-api`.

Existe também `flask create-db`, que cria as tabelas direto dos modelos. É atalho de
desenvolvimento: em qualquer ambiente com dados, use Alembic.

---

## Configuração e segredos

`settings.toml` é versionado e **não contém segredos**: só valores padrão não sensíveis e
as duas listas de registro de módulos (`EXTENSIONS`, `API_V1_ROUTES`).

O arquivo tem um `[default]` e uma seção por processo (`[api]`, `[bronze_api]`, `[consumer]`,
`[setup]`), o **perfil**. A variável `FLASK_PROCESS` escolhe o perfil, e as chaves dele valem
sobre as do `[default]`; sem ela, vale só o `[default]`, que carrega tudo (desenvolvimento e
testes). Um `FLASK_PROCESS` com nome que não é seção do arquivo reprova a subida.

Valores sensíveis vêm de variáveis de ambiente com prefixo `FLASK_`, carregadas do `.env`
(que está no `.gitignore`):

| Variável de ambiente | Vira em `app.config` |
|---|---|
| `FLASK_SECRET_KEY` | `SECRET_KEY` |
| `FLASK_JWT_SECRET_KEY` | `JWT_SECRET_KEY` |
| `FLASK_SQLALCHEMY_DATABASE_URI` | `SQLALCHEMY_DATABASE_URI` |
| `FLASK_MONGO_URI` | `MONGO_URI` (processos com `ext.mongo`) |
| `FLASK_AMQP_URL` | `AMQP_URL` (processos com `ext.rabbitmq`) |
| `FLASK_AMQP_MANAGEMENT_URL` | `AMQP_MANAGEMENT_URL` (só o `setup`, para o `flask rabbitmq setup`) |
| `FLASK_PROCESS` | `PROCESS` (o perfil; não é segredo, mas é por processo) |
| `FLASK_TEST_DATABASE_URI`, `FLASK_TEST_MONGO_URI`, `FLASK_TEST_AMQP_URL`, `FLASK_TEST_AMQP_MANAGEMENT_URL` | usados pela suíte |

Precedência: variável de ambiente > `.env` > `settings.toml`. A aplicação falha na
inicialização, com mensagem explícita, se a URI do banco ou a chave JWT estiverem vazias, e
também a URI do Mongo ou a URL do RabbitMQ, nos processos que os usam.

### Dependências

`requirements.txt` é a lista editada à mão (com faixas de versão).
`requirements.lock.txt` congela o conjunto exato verificado e **é o que o Docker instala** —
sem ele, o build de amanhã poderia puxar uma versão diferente da testada hoje. Para
regenerar depois de mexer no `requirements.txt`, veja o comando no cabeçalho do lock.

### Pool de conexões e workers

A conta que importa:

```
(workers do gunicorn) × (pool_size + max_overflow)  ≤  max_connections do Postgres
```

Com os padrões (`WEB_CONCURRENCY=2`, `pool_size=5`, `max_overflow=5`): 20 conexões no pior
caso, folgado para o padrão de 100 do Postgres. Ao aumentar `WEB_CONCURRENCY`, refaça a
conta.

O número de workers vem de `WEB_CONCURRENCY` (lido nativamente pelo gunicorn), não de um
flag fixo na imagem. **Não use `--preload`** sem descartar o engine no `post_fork`: os
workers herdariam os mesmos sockets do pool e corromperiam a comunicação com o banco.

---

## Segurança e observabilidade — o essencial

Autenticação é exigida **por padrão**: o requisito é global no spec OpenAPI e aplicado por
classe de rota. Rotas públicas abrem mão explicitamente. Hoje só `/auth/token`, `/health/` e
`/health/live` são públicas — e é `ROTAS_PUBLICAS`, em `tests/test_contracts.py`, que guarda essa lista: o
CI compara com o que o spec declara aberto, então nenhuma rota fica pública por descuido.

Autorização é por **permissão**, não por papel: cada rota declara a capacidade que exige
(`permission_required(Permission.ITEM_ESCREVER)`), e os papéis são pacotes de permissões
definidos em `app/domain/authz.py`. Falta de permissão para a operação responde **403**; recurso
que existe mas é de outro dono responde **404**. O papel é lido do banco a cada
requisição, então promover ou rebaixar alguém vale na hora, sem esperar o token expirar.

Toda requisição recebe um id: devolvido no header `X-Request-ID`, incluído no envelope de
erro, em toda linha do log de acesso e nas linhas de erro. É o que torna um problema em
produção investigável — o cliente relata o `request_id` que recebeu e você reconstrói o que
aconteceu:

```bash
docker compose logs api | grep <request_id>
# 172.19.0.1 "GET /api/v1/item/ HTTP/1.1" 401 106 3187us req=<request_id>
```

Detalhes e o raciocínio por trás:
[documentacao/02-decisoes.md](documentacao/02-decisoes.md).

---

## Antes de ir para produção

- [ ] Trocar todos os segredos do `.env` e nunca reutilizar os de desenvolvimento.
- [ ] Restringir `CORS_ORIGINS` no `settings.toml` para os domínios do seu frontend
      (o padrão é `["*"]`, adequado só para desenvolvimento).
- [ ] Remover do `docker-compose.yml` as conveniências de desenvolvimento: o mapeamento das
      portas `5432` do `db`, `27017` do `mongo` e `5672`/`15672` do `rabbitmq` (existem para a
      suíte e as ferramentas alcançarem os serviços a partir do host), o bind mount
      `./migrations` do serviço `api` (existe para que as revisões geradas dentro do
      container apareçam no host) e o limite de cache do Mongo (`--wiredTigerCacheSizeGB`,
      dimensionado para o Docker Desktop).
- [ ] Dar a cada processo um usuário próprio, com permissões mínimas, no Mongo e no RabbitMQ.
      Hoje o `bronze-api` usa o usuário root do Mongo e o `api` usa o administrador do RabbitMQ.
- [ ] Trocar o Mongo e o RabbitMQ de nó único por replica set e cluster.
- [ ] Conferir que o build de produção usa `target: runtime` — o estágio `dev` carrega as
      ferramentas de teste e não deve ir para produção.
- [ ] Rever `JWT_ACCESS_TOKEN_EXPIRES` (padrão: 1 hora) e considerar refresh tokens.
- [ ] Revisar `ROLE_PERMISSIONS` em `app/domain/authz.py` — os papéis que acompanham a base são um
      ponto de partida, não um modelo de permissões pronto — e conferir quem está como
      `admin`: `flask shell` ou `SELECT name, role FROM users WHERE role <> 'usuario';`.
- [ ] Ajustar `WEB_CONCURRENCY` à CPU disponível e refazer a conta do pool de conexões.
- [ ] Apontar o readiness probe do orquestrador para `/api/v1/health/` e o liveness probe para
      `/api/v1/health/live`. O liveness não consulta dependência nenhuma: um banco fora não
      volta reiniciando o processo.
- [ ] Não deixar o `/api/v1/health/` aberto na internet: bloqueie-o no proxy reverso ou limite-o
      à rede interna. Ele é público e, a cada chamada, sonda as dependências (abre uma conexão
      AMQP e faz ping no Mongo), o que pode prender um worker por até
      `HEALTH_PROBE_TIMEOUT_SECONDS` por sonda. O `/health/live` não tem esse custo.
- [ ] Colocar um proxy reverso com TLS na frente, repassando o `X-Request-ID`.
- [ ] Remover a entidade `Item`, seus schemas, testes e a migração inicial depois que suas
      entidades estiverem no lugar.
