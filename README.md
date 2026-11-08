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
# SECRET_KEY e JWT_SECRET_KEY, e preencha as senhas dos processos: RABBITMQ_PRODUCER_PASSWORD,
# RABBITMQ_CONSUMER_PASSWORD, MONGO_WRITER_PASSWORD e MONGO_READER_PASSWORD
# gere cada segredo com (use 32 bytes ou mais):
#   python -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose up --build
```

Isso sobe o PostgreSQL (e cria o banco de testes), o MongoDB e o RabbitMQ. Depois roda o job
`setup`, que aplica as migrações, a topologia do RabbitMQ (os exchanges `telemetry` e
`telemetry.dlx`, a fila `bronze.telemetry`, a DLQ `bronze.telemetry.dlq` e a policy que liga
as duas), os índices do bronze e o usuário de cada processo, e termina. Só então inicia os dois processos HTTP, o `api`
(porta 5000) e o `bronze-api` (porta 5001), e o `consumer`, que grava no bronze o que chega na
fila. O compose falha com mensagem explícita se algum segredo do `.env` estiver faltando —
nunca sobe com segredo vazio.

As senhas entram nas URLs de conexão que o compose monta: use só letras, números, `-` e `_`
(o `token_urlsafe` já gera assim). As de administração do Postgres, do Mongo e do RabbitMQ
valem só na primeira subida de cada volume, que é quando o usuário é criado; para trocá-las
depois, apague o volume do serviço com `docker volume rm` (ver `.env.example`). As dos usuários
por processo o job `setup` reaplica a cada subida: basta trocar no `.env`.

Crie o primeiro usuário e experimente:

```bash
docker compose exec api flask create-user --name admin --password sua-senha-aqui --role admin
```

Os papéis são `usuario`, `admin` (o `usuario` mais os veículos de todos) e `silver` (a conta de
sistema da camada silver: só lê o bronze, e só a CLI a cria); o padrão é `usuario`, e é o que o
cadastro público cria. Para trocar o papel de alguém depois: `flask set-role --name joao --role
admin` (vale na requisição seguinte, mesmo com o token já emitido). Para trocar uma senha:
`flask set-password --name joao`, que pede a senha nova (a opção `--password` ficaria no histórico
do shell) e encerra todas as sessões da conta.

- **Swagger UI:** http://localhost:5000/api/v1/ (e http://localhost:5001/api/v1/, o `bronze-api`)
- **Especificação OpenAPI:** http://localhost:5000/api/v1/openapi.json
- **Health:** http://localhost:5000/api/v1/health/ (uma chave por dependência do processo) e
  `/api/v1/health/live` (só o processo)
- **Painel do RabbitMQ:** http://localhost:15672, com `RABBITMQ_ADMIN_USER` e a senha do `.env`

| Processo | `FLASK_PROCESS` | Porta | Dependências | Sem ela, o `/health/` responde |
|---|---|---|---|---|
| `api` | `api` | 5000 | Postgres, RabbitMQ | 503 sem o Postgres; 200 `degraded` sem o RabbitMQ |
| `bronze-api` | `bronze_api` | 5001 | Postgres, Mongo | 503 sem qualquer um dos dois (e a leitura do bronze, 503 sem o Mongo) |
| `consumer` | `consumer` | — | Mongo, RabbitMQ | não tem `/health/`: responde em `:8081/health/live` e `/health/ready`, só dentro do container. Sem o Mongo, pausa e as mensagens esperam na fila; sem o RabbitMQ, fica de pé, não pronto, e reconecta sozinho |
| `setup` (job) | `setup` | — | Postgres, RabbitMQ (API de gerenciamento), Mongo | termina depois de migrar e aplicar a topologia e os índices |

```bash
# criar uma conta (público; nasce com o papel usuario)
curl -X POST http://localhost:5000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"ana","password":"outra-senha-aqui"}'

# obter o par de tokens: access_token (15 min) e refresh_token (30 dias)
curl -X POST http://localhost:5000/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"name":"admin","password":"sua-senha-aqui"}'

# usar o token de acesso
curl http://localhost:5000/api/v1/vehicle/ -H "Authorization: Bearer <token>"

# o token de acesso expirou: trocar o de renovação por um par novo
curl -X POST http://localhost:5000/api/v1/auth/refresh -H "Authorization: Bearer <refresh_token>"

# encerrar a sessão (204)
curl -X POST http://localhost:5000/api/v1/auth/logout -H "Authorization: Bearer <refresh_token>"

# cadastrar um veículo e enviar um lote de leituras dele (202: o lote está na fila)
curl -X POST http://localhost:5000/api/v1/vehicle/ -H "Authorization: Bearer <token>" \
  -H 'Content-Type: application/json' -d '{"vin":"93YBSR7RHEJ298116"}'
curl -X POST http://localhost:5000/api/v1/telemetry/batches -H "Authorization: Bearer <token>" \
  -H 'Content-Type: application/json' -d '{"records":[{"id":"9f1c2a7e-6b4d-4d0e-9a51-2f6f1b0c8e11",
  "vin":"93YBSR7RHEJ298116","collected_at":"2026-11-02T14:03:11.482Z",
  "readings":[{"metric":"rpm","unit":"rpm","available":true,"value":1065.0}]}]}'

# fazer de outra pessoa motorista do veículo, pelo login dela: ela passa a poder enviar
curl -X POST http://localhost:5000/api/v1/vehicle/<id>/drivers -H "Authorization: Bearer <token>" \
  -H 'Content-Type: application/json' -d '{"user_name":"joao"}'
# como o motorista: os veículos que ele dirige, e a saída de um deles
curl http://localhost:5000/api/v1/vehicle/driving -H "Authorization: Bearer <token do joao>"
curl -X DELETE http://localhost:5000/api/v1/vehicle/driving/<id> \
  -H "Authorization: Bearer <token do joao>"
```

O lote é aceito em parte: o registro de um veículo de que você não é o gestor nem motorista
ativo volta em `rejected`, e os outros seguem para a fila. O gestor remove um motorista com
`DELETE /api/v1/vehicle/<id>/drivers/<id do usuário>`, e excluir o veículo desliga todos. Com
o RabbitMQ fora, a resposta é 503 com `Retry-After`, e o lote pode ser reenviado inteiro (cada
registro é gravado uma vez só, pelo `id`). O `consumer` grava cada registro aceito como um documento da coleção
`telemetry_records`, no banco `drive_data_bronze`, e só então confirma a mensagem. Para ver:

```bash
# os documentos do bronze (a senha do Mongo não sai do container)
docker compose exec -T mongo sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME"   -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin drive_data_bronze   --eval "db.telemetry_records.find().sort({_id: -1}).limit(3)"'
docker compose logs consumer        # uma linha por lote: novos e repetidos
```

No painel do RabbitMQ (aba *Queues*), a `bronze.telemetry` drena e a `bronze.telemetry.dlq`
fica em 0. Uma mensagem na DLQ estava fora do contrato ou falhou mais que
`AMQP_DELIVERY_LIMIT` vezes, e o log do consumer diz qual. Com o consumer parado
(`docker compose stop consumer`), o `api` continua respondendo 202 e a fila acumula; quando ele
volta (`docker compose start consumer`), ela é drenada, sem duplicar nada.

**O que cada queda faz.** Nada aceito num 202 se perde em nenhuma delas:

| Quem cai | `api` | `consumer` | `bronze-api` |
|---|---|---|---|
| `consumer` | segue dando 202; a fila acumula | — | segue |
| RabbitMQ | o envio dá 503 com `Retry-After`; o resto segue, com o health `degraded` | de pé e não pronto; reconecta sozinho | segue |
| Mongo | segue dando 202 | de pé e não pronto; as mensagens esperam na fila | 503 |

A saúde do consumer fica em `http://localhost:8081/health/ready`, dentro do container (é o
healthcheck do compose). Com a fila no teto (`AMQP_QUEUE_MAX_BYTES`, 2 GiB), o envio também dá 503.

**A DLQ** guarda o que o consumer não conseguiu gravar. Ela não tem consumidor e pede ação:

```bash
docker compose run --rm -T --no-deps --entrypoint flask setup rabbitmq dlq-list --limit 20
# depois de corrigir a causa:
docker compose run --rm -T --no-deps --entrypoint flask setup rabbitmq dlq-requeue --limit 20
```

Mudou `AMQP_QUEUE_MAX_BYTES` ou `AMQP_DELIVERY_LIMIT`? Rode o `setup` de novo
(`docker compose up -d setup`), porque os dois vão na policy da fila.

A camada silver lê o bronze pelo `bronze-api` (porta 5001), com uma conta de sistema de papel
`silver`, que só um operador cria, pela CLI. A senha é um segredo de pelo menos 32 caracteres,
gerado por `python -c "import secrets; print(secrets.token_urlsafe(32))"` e guardado no cofre
da silver. Para trocá-lo: `flask set-password --name silver-etl`.

```bash
docker compose exec api flask create-user --name silver-etl --role silver   # pede a senha
curl -X POST http://localhost:5001/api/v1/auth/token \
  -H 'Content-Type: application/json' -d '{"name":"silver-etl","password":"a-senha-da-silver"}'

# do começo do bronze; depois, de onde parou, com o next_cursor da resposta anterior
curl "http://localhost:5001/api/v1/bronze/records?limit=500" -H "Authorization: Bearer <token>"
curl "http://localhost:5001/api/v1/bronze/records?limit=500&cursor=<next_cursor>" \
  -H "Authorization: Bearer <token>"
```

A resposta traz `items` (o bruto e os metadados de cada registro), `next_cursor`, `has_more` e
`watermark`. Os registros saem na ordem em que o bronze os gravou, e só os gravados há mais de
`BRONZE_SETTLE_SECONDS` (30 s): é o que garante que nenhum registro grave atrás de um cursor
que a silver já passou. A silver guarda o `next_cursor` só depois de processar a página, chama
de novo em seguida enquanto `has_more` for verdadeiro e, quando não for, espera o intervalo de
polling. Uma queda entre processar e guardar repete a página: ela deduplica por `record_id`.
Com o Mongo fora, a resposta é 503, e a chamada se repete com o mesmo cursor. Nem o `admin`
nem o `usuario` leem o bronze (403).

A leitura aceita filtros: `vin`, `metric` (repetível, até 10), `collected_from` e `collected_to`.
O começo pode ser um `ingested_from`, em vez do cursor. O cursor só vale para os filtros com que
foi emitido: mudou o filtro, comece de novo, ou a resposta é 422. O período de coleta não tem
índice, então combine um recorte grande com `vin` ou `ingested_from`.

```bash
curl "http://localhost:5001/api/v1/bronze/records?vin=93YBSR7RHEJ298116&metric=rpm&metric=speed" \
  -H "Authorization: Bearer <token>"
```

O registro que o consumer não grava não some: um `id` já enviado por outra pessoa ou um VIN fora
da forma ficam em `telemetry_rejections`, por 30 dias, com o motivo e o bruto (a coleção é a mesma
consulta do `mongosh` acima, com `db.telemetry_rejections.find()`).

### Rodando fora do Docker

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
# no .env, descomente FLASK_SQLALCHEMY_DATABASE_URI, FLASK_MONGO_URI, FLASK_AMQP_URL,
# FLASK_AMQP_MANAGEMENT_URL (apontam para localhost) e as quatro senhas dos processos, que os
# comandos de setup aplicam, e suba as dependências:
# docker compose up -d db mongo rabbitmq
alembic upgrade head                          # o banco, na raiz do repositório
cd app && flask rabbitmq setup && flask mongo setup   # a topologia da fila e os índices do bronze
flask run                                     # a API
flask consumer run                            # noutro terminal: o consumer (Ctrl+C para parar)
```

Sem `FLASK_PROCESS`, vale o perfil `[default]`, que carrega tudo e por isso pede as três URIs.
Com `FLASK_PROCESS=api` no `.env`, a aplicação sobe como o processo `api` e nem pede o Mongo.

---

## Estrutura

```
app/
  app.py                  create_app(): factory; não conhece nenhuma extensão
  instrumentation/        observação (sondas do health, logs JSON, correlação, métricas, saúde do consumer)
  messaging/              RabbitMQ: conexão, sonda, API de gerenciamento, topologia, publisher e consumer
  ext/                    infraestrutura — cada módulo expõe init_app(app)
  domain/                 modelos ORM e o vocabulário puro (papéis e permissões, VIN, portas, contrato da fila)
  repository/             acesso a dados, uma subpasta por tecnologia (sql/: o único lugar que monta SQL;
                          mongo/: o bronze)
  service/                regra de negócio, validação, ownership; telemetry/: o pipeline, só com portas
  schemas/                contrato de entrada/saída (marshmallow)
  blueprint/              camada HTTP: envelope de erro e, em restapi/v1/, as rotas (uma por recurso)
migrations/               Alembic
tests/                    pytest: a raiz usa Postgres; unit/, mongo/, rabbitmq/ e e2e/ (os três), o que o nome diz
docker/                   entrypoint, setup.sh (o job de preparação: migrações, topologia e índices) e,
                          por serviço, os scripts de init
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
| `FLASK_AMQP_PRODUCER_PASSWORD`, `FLASK_AMQP_CONSUMER_PASSWORD` | as senhas que o `flask rabbitmq setup` aplica aos usuários dos processos (só o `setup`) |
| `FLASK_MONGO_WRITER_PASSWORD`, `FLASK_MONGO_READER_PASSWORD` | as senhas que o `flask mongo setup` aplica aos usuários do bronze (só o `setup`) |
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
classe de rota. Rotas públicas abrem mão explicitamente. Hoje só `/auth/token`, `/auth/register`,
`/health/` e `/health/live` são públicas — e é `ROTAS_PUBLICAS`, em `tests/test_contracts.py`, que guarda essa lista: o
CI compara com o que o spec declara aberto, então nenhuma rota fica pública por descuido.

Autorização é por **permissão**, não por papel: cada rota declara a capacidade que exige
(`permission_required(Permission.VEICULO_ESCREVER)`), e os papéis são pacotes de permissões
definidos em `app/domain/authz.py`. Falta de permissão para a operação responde **403**; recurso
que existe mas é de outro dono responde **404**. O papel é lido do banco a cada
requisição, então promover ou rebaixar alguém vale na hora, sem esperar o token expirar.

Toda requisição recebe um id: devolvido no header `X-Request-ID`, incluído no envelope de
erro e em toda linha de log, inclusive nas do consumer, porque ele viaja na mensagem da fila.
É o que torna um problema em produção investigável — o cliente relata o `request_id` que
recebeu e você reconstrói o que aconteceu:

```bash
docker compose logs api consumer | grep <request_id>
# {"ts": "...", "level": "INFO", "logger": "http.access", "message": "POST /api/v1/telemetry/batches 202",
#  "process": "api", "request_id": "<request_id>", "user_id": 51, "event": "http.access", "status": 202, ...}
# {"ts": "...", "logger": "instrumentation.metrics", "process": "consumer", "request_id": "<request_id>",
#  "message_id": "...", "event": "bronze.batch.ingested", "inserted": 3, ...}
```

Os logs saem em JSON, uma linha por evento. Para ler localmente, `FLASK_LOG_FORMAT=text`.

Métricas e painéis ficam num perfil à parte do compose:

```bash
docker compose --profile observabilidade up -d
# Prometheus em http://localhost:9090 (alertas em /alerts)
# Grafana em http://localhost:3000 (admin / GRAFANA_ADMIN_PASSWORD), painel "Pipeline de telemetria"
```

Cada processo expõe `/metrics`: o `api` e o `bronze-api` na porta da aplicação, e o `consumer`
em `:8081`, com as réplicas descobertas pelo DNS do compose. A taxa de descarte:

```promql
sum(rate(ingest_records_total{outcome="rejected"}[5m])) / sum(rate(ingest_records_total[5m]))
```

O que recusa no consumer está em `bronze_records_total{outcome=~"collision|invalid"}`. Os
alertas e o porquê de cada limite estão em `documentacao/02-decisoes.md`, "Os alertas do
pipeline".

Detalhes e o raciocínio por trás:
[documentacao/02-decisoes.md](documentacao/02-decisoes.md).

---

## Antes de ir para produção

- [ ] Trocar todos os segredos do `.env` e nunca reutilizar os de desenvolvimento.
- [ ] Antes de aplicar a migração 0006, conferir as contas que mudam de papel:
      `SELECT name, role FROM users WHERE role IN ('leitor', 'gerente')`. Elas viram `usuario`:
      o `leitor` passa a escrever nos próprios veículos e o `gerente` deixa de ler os de todos.
- [ ] Manter `CORS_ORIGINS = []` no `settings.toml` enquanto só o app mobile e a silver usarem
      a API. Um frontend web entra com os domínios dele, nunca com `*`.
- [ ] Remover do `docker-compose.yml` as conveniências de desenvolvimento: o mapeamento das
      portas `5432` do `db`, `27017` do `mongo` e `5672`/`15672` do `rabbitmq` (existem para a
      suíte e as ferramentas alcançarem os serviços a partir do host), o bind mount
      `./migrations` do serviço `api` (existe para que as revisões geradas dentro do
      container apareçam no host) e o limite de cache do Mongo (`--wiredTigerCacheSizeGB`,
      dimensionado para o Docker Desktop).
- [ ] Confirmar o prazo de retenção do bronze (`BRONZE_RETENTION_DAYS`, 365 dias) com o jurídico,
      à luz da LGPD, e decidir o que acontece com os dados de uma conta excluída. Reduzir o
      prazo apaga de verdade na passada seguinte do TTL, e 0 desliga a retenção.
- [ ] Ligar TLS no AMQP, na API de gerenciamento e no Mongo (`amqps://`, `https://` e
      `tls=true`) assim que eles saírem da rede interna do compose: as senhas dos processos e o
      dado bruto passam por ali.
- [ ] Limitar a taxa no proxy reverso, ao menos em `/auth/token`, `/auth/register` e
      `/telemetry/batches`: a aplicação não tem rate limit, e o cadastro é público.
- [ ] Deixar o `bronze-api` só na rede interna, tirando o `5001:5000` do `docker-compose.yml`: quem
      o usa é a silver, e o dado bruto sai por ali.
- [ ] Definir a rotina de troca das senhas dos processos: mude a senha no `.env` e rode
      `docker compose up -d`. O job `setup` a aplica no broker e no Mongo, e os processos
      são recriados com a nova.
- [ ] Trocar o Mongo e o RabbitMQ de nó único por replica set e cluster.
- [ ] Conferir que o build de produção usa `target: runtime` — o estágio `dev` carrega as
      ferramentas de teste e não deve ir para produção.
- [ ] Calibrar os limites do envio com o volume real: `MAX_CONTENT_LENGTH` (1 MiB) no
      `settings.toml`, e os 500 registros e 64 leituras no `app/schemas/telemetry_schema.py`.
- [ ] Dimensionar `AMQP_QUEUE_MAX_BYTES` (2 GiB) pelo disco do broker e pelo tempo de queda do
      consumer que se quer absorver, e alertar quando a DLQ tiver qualquer mensagem.
- [ ] Rever `JWT_ACCESS_TOKEN_EXPIRES` (padrão: 15 min) e `JWT_REFRESH_TOKEN_EXPIRES` (30 dias),
      e agendar o `flask prune-tokens`, que apaga da tabela os tokens de renovação expirados.
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
- [ ] Colocar um proxy reverso com TLS na frente, repassando o `X-Request-ID` e bloqueando o
      `/metrics`, que é público para o Prometheus alcançá-lo pela rede interna.
- [ ] Definir `GRAFANA_ADMIN_PASSWORD` no `.env` e ligar um Alertmanager: o Prometheus do
      perfil `observabilidade` avalia os alertas, mas ninguém é avisado.
