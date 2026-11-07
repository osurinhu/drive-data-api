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
| Migrações | Alembic |
| Autenticação | Flask-JWT-Extended |
| Injeção de dependência | `dependency-injector` |
| Configuração | Dynaconf (`settings.toml` + variáveis de ambiente) |
| Testes | pytest (contra PostgreSQL real) |
| Lint / formatação | ruff |
| Tipos | mypy (camadas de lógica pura) |
| Execução | Gunicorn em Docker, orquestrado por docker-compose |

---

## Começando

Pré-requisitos: Docker e Docker Compose.

```bash
cp .env.example .env
# edite o .env: troque POSTGRES_PASSWORD, SECRET_KEY e JWT_SECRET_KEY
# gere cada segredo com (use 32 bytes ou mais):
#   python -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose up --build
```

Isso sobe o PostgreSQL, cria o banco de testes, aplica as migrações e inicia a API. O
compose falha com mensagem explícita se algum segredo do `.env` estiver faltando — nunca
sobe com segredo vazio.

Crie o primeiro usuário e experimente:

```bash
docker compose exec api flask create-user --name admin --password sua-senha-aqui --role admin
```

Os papéis são `leitor` (só leitura), `usuario`, `gerente` e `admin`; o padrão é `usuario`.
Para trocar o papel de alguém depois: `flask set-role --name admin --role gerente` (vale na
requisição seguinte, mesmo com o token já emitido).

- **Swagger UI:** http://localhost:5000/api/v1/
- **Especificação OpenAPI:** http://localhost:5000/api/v1/openapi.json
- **Health:** http://localhost:5000/api/v1/health/

```bash
# obter um token
curl -X POST http://localhost:5000/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"name":"admin","password":"sua-senha-aqui"}'

# usar o token
curl http://localhost:5000/api/v1/item/ -H "Authorization: Bearer <token>"
```

### Rodando fora do Docker

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
# defina FLASK_SQLALCHEMY_DATABASE_URI no .env apontando para localhost
cd app && flask run
```

---

## Estrutura

```
app/
  app.py                  create_app(): factory; não conhece nenhuma extensão
  http_errors.py          envelope de erro + mapa exceção → HTTP
  request_context.py      id da requisição
  authz.py                papéis, permissões e o mapa entre eles
  ext/                    infraestrutura — cada módulo expõe init_app(app)
  domain/                 modelos ORM
  repository/             acesso a dados — única camada que monta SQL
  service/                regra de negócio, validação, ownership
  schemas/                contrato de entrada/saída (marshmallow)
  blueprint/restapi/v1/   rotas — uma por recurso
migrations/               Alembic
tests/                    pytest
docker/                   entrypoint e scripts de init do banco
settings.toml             configuração (sem segredos) + registro de módulos
requirements.lock.txt     versões exatas do build (versionado)
documentacao/             arquitetura, decisões e guias de extensão
```

Detalhamento de cada camada e das regras de dependência:
**[documentacao/01-arquitetura.md](documentacao/01-arquitetura.md)**.

---

## Testes

A suíte roda contra **PostgreSQL de verdade**, não SQLite. O banco `<POSTGRES_DB>_test` é
criado automaticamente na primeira subida do compose.

```bash
# no container (não precisa de Python local)
docker compose run --rm test

# ou localmente, contra o Postgres do compose
docker compose up -d db
pytest
```

As tabelas são esvaziadas depois de cada teste, então a ordem de execução não importa.

```bash
ruff check .
ruff format .
mypy
```

O CI (`.github/workflows/ci.yml`) roda tudo isso em cada push, com um PostgreSQL de
serviço, e inclui `alembic check` — que reprova se as migrações divergirem dos modelos.

---

## Migrações

```bash
# criar uma migração a partir das mudanças nos modelos
docker compose exec api alembic revision --autogenerate -m "descrição"

# aplicar (o entrypoint do container já faz isso no boot)
docker compose exec api alembic upgrade head

# voltar uma versão
docker compose exec api alembic downgrade -1

# conferir se modelos e migrações estão em sincronia
docker compose exec api alembic check
```

O `migrations/env.py` lê a URL do banco da configuração da aplicação — a string de conexão
não é duplicada no `alembic.ini`. Ele carrega **apenas** a configuração, não a aplicação
inteira: migrar o banco não deve exigir que segredos de JWT e CORS estejam definidos.

Existe também `flask create-db`, que cria as tabelas direto dos modelos. É atalho de
desenvolvimento: em qualquer ambiente com dados, use Alembic.

---

## Configuração e segredos

`settings.toml` é versionado e **não contém segredos**: só valores padrão não sensíveis e
as duas listas de registro de módulos (`EXTENSIONS`, `API_V1_ROUTES`).

Valores sensíveis vêm de variáveis de ambiente com prefixo `FLASK_`, carregadas do `.env`
(que está no `.gitignore`):

| Variável de ambiente | Vira em `app.config` |
|---|---|
| `FLASK_SECRET_KEY` | `SECRET_KEY` |
| `FLASK_JWT_SECRET_KEY` | `JWT_SECRET_KEY` |
| `FLASK_SQLALCHEMY_DATABASE_URI` | `SQLALCHEMY_DATABASE_URI` |
| `FLASK_TEST_DATABASE_URI` | `TEST_DATABASE_URI` (usado pela suíte) |

Precedência: variável de ambiente > `.env` > `settings.toml`. A aplicação falha na
inicialização, com mensagem explícita, se a URI do banco ou a chave JWT estiverem vazias.

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
classe de rota. Rotas públicas abrem mão explicitamente. Hoje só `/auth/token` e `/health/`
são públicas — e é `ROTAS_PUBLICAS`, em `tests/test_contracts.py`, que guarda essa lista: o
CI compara com o que o spec declara aberto, então nenhuma rota fica pública por descuido.

Autorização é por **permissão**, não por papel: cada rota declara a capacidade que exige
(`permission_required(Permission.ITEM_ESCREVER)`), e os papéis são pacotes de permissões
definidos em `app/authz.py`. Falta de permissão para a operação responde **403**; recurso
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
- [ ] Remover do `docker-compose.yml` as duas conveniências de desenvolvimento: o
      mapeamento da porta `5432` do serviço `db` (existe para a suíte de testes alcançar o
      banco a partir do host) e o bind mount `./migrations` do serviço `api` (existe para
      que as revisões geradas dentro do container apareçam no host).
- [ ] Conferir que o build de produção usa `target: runtime` — o estágio `dev` carrega as
      ferramentas de teste e não deve ir para produção.
- [ ] Rever `JWT_ACCESS_TOKEN_EXPIRES` (padrão: 1 hora) e considerar refresh tokens.
- [ ] Revisar `ROLE_PERMISSIONS` em `app/authz.py` — os papéis que acompanham a base são um
      ponto de partida, não um modelo de permissões pronto — e conferir quem está como
      `admin`: `flask shell` ou `SELECT name, role FROM users WHERE role <> 'usuario';`.
- [ ] Ajustar `WEB_CONCURRENCY` à CPU disponível e refazer a conta do pool de conexões.
- [ ] Apontar o readiness/liveness probe do orquestrador para `/api/v1/health/`.
- [ ] Colocar um proxy reverso com TLS na frente, repassando o `X-Request-ID`.
- [ ] Remover a entidade `Item`, seus schemas, testes e a migração inicial depois que suas
      entidades estiverem no lugar.
