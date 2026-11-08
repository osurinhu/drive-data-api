# Arquitetura

## Os dois eixos

A arquitetura tem **duas** dimensões independentes. Confundi-las é a origem da maior parte
da confusão em projetos assim.

**Eixo vertical — por onde a requisição passa.** Quatro camadas, cada uma conhecendo
apenas a de baixo:

```mermaid
flowchart LR
    C["Cliente<br/>HTTP"] --> R["Rota<br/>(MethodView)"]
    R --> S["Serviço"]
    S --> P["Repositório"]
    P --> M["Modelo<br/>(ORM)"]
    M --> DB[("PostgreSQL")]
    R -.->|serializa| SC["Schema"]
```

**Eixo horizontal — como a aplicação é montada.** Acontece uma única vez, na
inicialização. O `create_app()` não conhece nenhuma extensão nem nenhuma rota: ele lê duas
listas de caminhos de módulo do `settings.toml` e importa cada uma.

```mermaid
flowchart TB
    AF["create_app()"] --> CFG["ext.config<br/>carrega settings.toml + .env<br/>no perfil de FLASK_PROCESS"]
    CFG --> LOOP["para cada item de EXTENSIONS:<br/>import_module(x).init_app(app)"]
    LOOP --> E1["ext.observability"]
    LOOP --> E2["ext.database · ext.mongo<br/>ext.rabbitmq"]
    LOOP --> E3["ext.container<br/>ext.consumer_container"]
    LOOP --> E4["ext.auth · ext.cors<br/>ext.error_handler · ext.commands<br/>ext.consumer"]
    LOOP --> E5["ext.api"]
    E5 --> ROTAS["para cada item de API_V1_ROUTES:<br/>registra o blueprint sob /api/v1"]
```

A consequência prática: **nenhuma funcionalidade nova exige editar `app.py`.** Você cria um
módulo e acrescenta uma linha na configuração.

### Um perfil por processo

A mesma imagem roda mais de um processo, e cada um carrega só o que usa. A variável
`FLASK_PROCESS` escolhe uma seção do `settings.toml`, o **perfil**. As chaves do perfil valem
sobre as do `[default]`, e uma lista declarada no perfil (`EXTENSIONS`, `API_V1_ROUTES`)
substitui a do `[default]` inteira. A extensão que fica fora da lista do processo não é
inicializada: não exige configuração, não cria cliente nem comando e não aparece no health
dele. A rota fora da lista nem é importada: o container liga só as rotas do perfil. Os
serviços, os repositórios e os adaptadores (o publisher, que importa o pika, e o leitor do
bronze, que importa o pymongo e o `ext.mongo`, a quem pede o cliente na hora do uso), porém,
ficam todos declarados no `ext/container.py`, comum aos processos HTTP. O `bronze-api` importa o
pika sem nunca conectar, porque só a rota de telemetria cria o publisher. O `api` importa o
módulo `ext.mongo` sem inicializá-lo nem conectar, porque só a rota de leitura do bronze cria o
leitor, e o perfil dele não lista a extensão (o `test_composicao.py` confere). O consumer
não usa esse container: o `ext/consumer_container.py` monta só o lado dele do pipeline (o
consumer da fila, o serviço de gravação e o adaptador do Mongo), e o processo não importa o
grafo do lado HTTP.

| Perfil | Quem roda | Dependências | Rotas |
|---|---|---|---|
| `[default]` (sem `FLASK_PROCESS`) | desenvolvimento local e a suíte de testes | todas: é a união dos perfis | todas |
| `[api]` | serviço `api` do compose: cadastro, login, as rotas de negócio e o recebimento dos lotes de telemetria, que ele publica na fila | Postgres e RabbitMQ | `health`, `auth`, `registration`, `vehicle`, `vehicle_driver`, `telemetry` |
| `[bronze_api]` | serviço `bronze-api`: a API da camada silver, que faz login e lê o bronze por cursor (`GET /bronze/records`) | Postgres e Mongo | `health`, `auth`, `bronze` |
| `[consumer]` | serviço `consumer` do compose (`flask consumer run`): consome a fila `bronze.telemetry` e grava cada registro no bronze | Mongo e RabbitMQ (sem Postgres e sem JWT) | nenhuma |
| `[setup]` | o job `setup`: migra, aplica a topologia do RabbitMQ e os índices do bronze e termina, antes dos outros processos subirem | Postgres, pelo Alembic, que lê a configuração sem montar a aplicação; RabbitMQ, pela API de gerenciamento (`flask rabbitmq setup`); Mongo (`flask mongo setup`) | nenhuma |

Um `FLASK_PROCESS` que não é seção do `settings.toml` reprova a subida: sozinho, o Dynaconf
cairia calado no `[default]`. O `tests/unit/test_composicao.py` fixa as fronteiras. Ele exige
que o `[default]` seja a união dos perfis e que nenhum perfil carregue o que o processo não
usa. Também monta processos à parte: confere que o consumer não importa SQLAlchemy, JWT, os
modelos, os repositórios SQL nem o lado do producer, e que cada processo HTTP só importa as
rotas do próprio perfil (o `bronze-api` não importa as do `api`, e o `api` não importa a leitura
do bronze). O que o processo montado não mostra (no consumer, um import que só um caminho raro
executa), o `tests/unit/test_fronteiras.py` pega lendo o código (ver "Producer e consumer",
abaixo).

---

## As camadas

A coluna mais importante é a última. É ela que impede a arquitetura de se dissolver com o
tempo.

| Camada | Pasta | Responsabilidade | **Não pode** |
|---|---|---|---|
| **Rota** | `app/blueprint/restapi/v1/` | Traduzir HTTP em chamada de serviço. Validar entrada por schema, devolver a saída. | Montar consulta, conter regra de negócio, usar `try/except` para erro de domínio |
| **Serviço** | `app/service/` (o pipeline de telemetria em `service/telemetry/`) | Regra de negócio, validação semântica, ownership, controle de transação. O CRUD segue o `BaseService`; o pipeline depende só de portas (`domain/ports.py`) e recebe os adaptadores pelo construtor. | Tocar no ORM direto, saber o que é HTTP (status, header, request). O pipeline, além disso, importar infraestrutura ou adaptador |
| **Repositório** | `app/repository/` (uma subpasta por tecnologia: `sql/` e `mongo/`) | Única camada que lê e grava dados: o SQL mora em `repository/sql/`, o bronze em `repository/mongo/`. Traduz intenção em consulta. Os adaptadores do Mongo implementam portas do pipeline (`BronzeWriter`, `BronzeReader`) e são os únicos que conhecem o layout do documento. | Conter regra de negócio ou decidir permissão; uma subpasta importar a tecnologia da outra |
| **Domínio** | `app/domain/` | Duas partes. O **ORM** (`base.py`, `models.py`) mapeia tabela para classe: colunas e relacionamentos. O **vocabulário** (todo o resto: `authz.py`, `vin.py`, `ports.py`, `contracts/`) são módulos folha que qualquer camada e qualquer processo importa. | O ORM: conter comportamento de negócio. O vocabulário: importar outra camada, o ORM ou infraestrutura |
| **Schema** | `app/schemas/` | Único contrato de entrada e saída da API. Valida e serializa. | Acessar banco |

O primeiro nível de pasta é o papel (a camada). O segundo nível é a tecnologia e só existe
quando o mesmo papel usa mais de uma (`repository/sql/` e `repository/mongo/`). Cada subpasta tem as próprias regras e
os próprios imports permitidos (ver 02-decisoes, "Um nível de pasta por papel, outro por
tecnologia").

E a infraestrutura, fora do caminho da requisição:

| Módulo | Papel |
|---|---|
| `app/ext/` | Extensões. **Cada módulo expõe exatamente uma função `init_app(app)`** — é essa convenção que permite o carregador tratá-las de forma uniforme. |
| `app/blueprint/http_errors.py` | Envelope de erro e o mapa exceção → status HTTP. Mora na camada HTTP, fora de `ext/`, porque é usado por duas extensões diferentes e pelas rotas; se morasse numa extensão, a outra passaria a depender dela. |
| `app/instrumentation/` | Peças de observação usadas por mais de uma extensão: o registro das sondas do health (`probes.py`; cada extensão que conecta um serviço registra a sua, e a rota do health executa as registradas) e o id da requisição (`request_context.py`), que vai para o log e para o envelope de erro. Fica fora de `ext/` pelo motivo do `http_errors.py`. |
| `app/messaging/` | Tudo que fala com o RabbitMQ: os parâmetros de conexão e a sonda (`connection.py`, com pika), o cliente da API de gerenciamento (`management.py`, só stdlib), a topologia (`topology.py`: fila, DLX, DLQ e a policy), o publisher dos lotes (`publisher.py`), que implementa a porta `TelemetryPublisher`, e o consumer da fila (`consumer.py`), que decodifica pelo contrato, entrega o lote a um handler e decide o destino de cada mensagem (ack, DLQ, de volta à fila). Recebe URL, prazos e o handler de quem chama e não importa `ext`, `repository`, `service` nem `blueprint`. |

---

## Regras de dependência

```
rota      →  serviço, schema, ext, instrumentation
serviço   →  repositório, domínio; o do sub-recurso, também o serviço do pai
service.telemetry → folhas do domínio (portas, contrato, VIN), service.errors, service.pagination
repository.sql   → domínio, ext.database
repository.mongo → folhas do domínio (as portas), ext.mongo
domínio   →  o ORM, o SQLAlchemy e o próprio domínio; as folhas, só outras folhas
schema    →  folhas do domínio
messaging →  folhas do domínio (o contrato e as portas)
instrumentation  →  (nada do projeto)
```

As extensões são a raiz de composição e importam das camadas o que precisam montar. Duas
peças novas são importadas quase só por elas: `instrumentation/`, por qualquer extensão, pela
rota do health, pelo envelope de erro (`blueprint/http_errors.py`, que lê o id da
requisição) e pela rota de telemetria (que põe o id no envelope da fila), e `messaging/`, pelo
`ext.rabbitmq` (a sonda e os comandos), pelo `ext.container` (o publisher), pelo
`ext.consumer_container` (o consumer) e pelo `ext.consumer` (o erro que encerra o comando).

Quando a extensão importa `blueprint.http_errors`, a dependência entre os dois pacotes corre
nos dois sentidos (a rota importa `ext.auth`, que importa `blueprint.http_errors`). Ela fica sem
ciclo porque o `blueprint/__init__.py` não importa nada e o `http_errors.py` não importa `ext`.
Mantenha as duas coisas assim.

Entre serviços, a seta corre num sentido só: o do sub-recurso recebe o serviço do pai (o
`VehicleDriverService` recebe o `VehicleService`), e o pai recebe só o **repositório** do
filho, para desligar os filhos no `before_delete`. O pai que chamasse o serviço do filho
fecharia um ciclo de import e de DI.

As sondas do health são a única ressalva ao "SQL só no repositório": o `SELECT 1` de
`ext/database.py` (e o `ping` de `ext/mongo.py`) verifica a conexão e não lê dado nenhum.

Duas regras que valem a pena memorizar:

1. **Nenhuma extensão importa outra extensão.** Se duas precisam da mesma coisa, essa coisa
   vira um módulo neutro (foi o caso de `blueprint/http_errors.py` e de
   `instrumentation/`) ou, se for vocabulário puro, uma folha do domínio. A ressalva são
   três peças de que o resto já depende, e que por isso outras extensões podem importar:
   `ext.config`, o carregador que o factory chama antes da lista (dele `ext.api` tira o
   `load_routes`), `ext.database`, dona do `db` que os repositórios SQL também importam (e
   que `ext.commands` usa), e `ext.mongo`, dona do cliente que os adaptadores de
   `repository/mongo/` importam (`mongo.db`). O `ext.mongo`, por sua vez, importa
   `repository.mongo.indexes` para o `flask mongo setup`: a dependência corre nos dois
   sentidos e fica sem ciclo porque o `indexes.py`, e o `bronze_document.py` que ele usa, não
   importam `ext`. Mantenha assim.
2. **A seta nunca aponta para cima.** Serviço não importa rota, repositório não importa
   serviço. Se você sentir essa necessidade, a lógica está na camada errada.

As **folhas do domínio** (`domain/authz.py`, `domain/vin.py`, `domain/ports.py`,
`domain/contracts/`) são a exceção
prevista pela regra 1: como só importam outras folhas, qualquer camada pode importá-las
sem criar ciclo nem acoplar duas camadas uma à outra. É o que permite rota, serviço,
modelo e schema falarem do mesmo vocabulário de permissões.

### Producer e consumer

**A fila é a única ligação entre o producer (`api`) e o consumer.** Do pipeline, os dois só
compartilham as folhas do domínio (o contrato da mensagem em `domain/contracts/` e as portas em
`domain/ports.py`) e os parâmetros de conexão com o broker (`messaging/connection.py`). O
consumer não lê o Postgres, não valida JWT e não importa os modelos ORM; o producer
não conhece o Mongo. O `tests/unit/test_fronteiras.py` percorre o **fecho de imports** de cada
lado (tudo o que o módulo carrega, direta ou indiretamente) e reprova se:

- o consumer (a partir de todas as extensões do perfil `[consumer]`) alcançar `blueprint`,
  `ext.auth`, `ext.container`, `ext.database`, `repository.sql`, o ORM,
  `service.telemetry.ingest_*`, `messaging.publisher` ou o lado da silver
  (`service.telemetry.bronze_read_service`, `repository.mongo.bronze_reader`);
- o producer (a partir da rota de telemetria, do `ingest_service` e do publisher) alcançar
  `ext.mongo`, `ext.consumer`, `repository.mongo`, `service.telemetry.bronze_*` ou
  `messaging.consumer`;
- a silver (a partir da rota do bronze, do `bronze_read_service` e do leitor) alcançar
  `ext.rabbitmq`, `ext.consumer`, `messaging`, o pika, `service.telemetry.ingest_*` ou o lado
  do consumer (`bronze_ingest_service`, `bronze_rules`, `repository.mongo.bronze_writer`);
- uma subpasta do repositório alcançar a tecnologia da outra, ou o serviço.

Os fechos do producer e da silver não seguem o `ext.container`: ele é a raiz de composição dos
dois processos HTTP e monta os adaptadores de ambos (o publisher e o leitor do bronze). Quem
decide o que cada processo conecta é o perfil, e o `test_composicao.py` confere o processo
montado. Do bronze, o writer (consumer) e o leitor (silver) compartilham só o cliente do
`ext.mongo`, o layout do documento (`bronze_document.py`) e o `server.py` (o prazo de cada
operação, a classificação da falha e a hora do servidor).

---

## As peças genéricas

Quase todo o CRUD já está escrito. Uma entidade nova herda:

| Classe | Arquivo | O que já dá de graça |
|---|---|---|
| `BaseModel` | `app/domain/base.py` | `id`, `created_at`, `last_update`, `deleted`, convenção de nomes de restrição |
| `BaseRepository[T]` | `app/repository/sql/base_repository.py` | `get_by_id`, `get_by_filter`, `get_first_by_filter`, `get_page`, `add`, `update`, `soft_delete`, `commit`, `rollback` |
| `BaseService[T]` | `app/service/base_service.py` | CRUD completo, ownership, exclusão lógica, transação, os ganchos `before_create` / `before_update` / `before_delete` e o `_clean_text` que eles usam para normalizar texto |

O parâmetro de tipo (`BaseRepository[Vehicle]`) é o que faz o type checker saber que
`get_by_id()` devolve `Vehicle | None`, e não `BaseModel | None`.

---

## Quatro mecanismos que convém entender antes de mexer

### Autorização

Uma **permissão** é uma capacidade (`veiculo:escrever`). Um **papel** é um pacote nomeado de
permissões, e é o papel que fica gravado em `users.role`. Os dois ficam em `app/domain/authz.py`,
junto do mapa entre eles:

```python
#: Cada papel parte do anterior, para que a hierarquia esteja escrita no código.
_USUARIO = frozenset({VEICULO_LER, VEICULO_ESCREVER, TELEMETRIA_ENVIAR})
_ADMIN   = _USUARIO | {VEICULO_LER_DE_TODOS, VEICULO_ESCREVER_DE_TODOS}

#: Ramo à parte: a conta de sistema da camada silver só lê o bronze.
_SILVER  = frozenset({BRONZE_LER, BRONZE_LER_DE_TODOS})

ROLE_PERMISSIONS = {
    Role.USUARIO: _USUARIO,
    Role.ADMIN: _ADMIN,
    Role.SILVER: _SILVER,
}
```

São três papéis. O `usuario` é o que o cadastro público cria, e o `admin` soma a ele os
escopos sobre os veículos de todos; o terceiro, `silver`, fica fora da hierarquia: não tem
nenhuma permissão dos outros, e nenhum deles, nem o `admin`, lê o bronze. Papel só muda pela
CLI: não existe caminho HTTP para escalar privilégio.

A rota declara a **permissão** que exige, nunca o papel:

```python
class VehicleView(MethodView):
    decorators = [permission_required(Permission.VEICULO_LER)]   # piso do recurso

    @permission_required(Permission.VEICULO_ESCREVER)            # o método exige mais
    def post(self, payload): ...
```

Três consequências que valem o espaço:

* **A hierarquia é inclusão de conjuntos, não um número.** `ADMIN` contém `USUARIO`; o
  `SILVER` é disjunto dos dois. Nenhum peso arbitrário para justificar, e "vê tudo mas altera
  só o que é dele" é expressável — o que um nível ordenado não permitiria.
* **O papel mora só no banco.** Não há claim equivalente no token: a autorização lê
  `current_user.role`, da linha que o `user_lookup_loader` já carregou. Trocar o papel vale
  na requisição seguinte, com o mesmo token.
* **Quem decide é uma declaração, não um `if`.** Mudar quem pode fazer o quê é reescrever
  o mapa, não caçar verificações espalhadas pelas rotas.

### Ownership

A permissão responde "pode fazer esta operação?"; o ownership responde "sobre **este**
registro?". São perguntas diferentes e têm respostas HTTP diferentes.

Entidades marcadas com `owned=True` pertencem a um usuário. O **escopo de dono** é
parâmetro de cada método do serviço, não do construtor — e **não tem valor padrão**:

```python
service.get_by_id(vehicle_id, current_owner_scope(Permission.VEICULO_LER_DE_TODOS))
```

`current_owner_scope(permissão)` (em `app/ext/auth.py`) devolve o id do usuário logado, ou
a sentinela `ANY_OWNER` (de `domain/authz.py`) se o papel dele tiver a permissão de escopo
informada. É o
**único** lugar onde o filtro de posse é dispensado, e a permissão vem explícita no ponto
de chamada para que a rota diga *de quem* ela opera sem precisar abrir o `auth.py`.

Ler e escrever têm permissões de escopo separadas (`VEICULO_LER_DE_TODOS`,
`VEICULO_ESCREVER_DE_TODOS`), que é o que permite o papel que enxerga tudo e altera só o
próprio.

| Situação | Resposta |
|---|---|
| O papel não tem a permissão da operação | **403** — decidido antes de tocar em dado algum, não revela nada |
| Tem a permissão, mas o recurso é de outro dono | **404** — um 403 confirmaria que o recurso existe |

O **sub-recurso** (os motoristas do veículo) não tem dono próprio: herda o do pai. O serviço
dele recebe o serviço do pai e começa toda operação do gestor por `get_by_id(pai_id,
owner_scope)`, que dá o 404 do pai de outro dono antes de o filho ser tocado. A visão da outra
ponta do vínculo (o motorista) não passa pelo pai: filtra pelo id dele, vindo do token
(03-como-estender §13).

### Erros

A rota não trata erro. O serviço levanta uma exceção de `app/service/errors.py`, e o mapa
`ERROR_STATUS` em `app/blueprint/http_errors.py` a traduz em status:

| Exceção | Status |
|---|---|
| `NotFoundError` | 404 |
| `ConflictError` | 409 |
| `BusinessRuleError` | 422 |
| `PermissionDeniedError` | 403 |
| `AuthenticationError` | 401 |
| `ServiceUnavailableError` | 503, com `Retry-After` quando a exceção traz o prazo |

Toda resposta de erro tem o mesmo formato, com o `request_id` que também aparece no log:

```json
{ "code": 404, "status": "Not Found",
  "message": "Veículo não encontrado.", "request_id": "a3f1c9..." }
```

A exceção é o 422 de validação: quem o monta é o próprio flask-smorest, que acrescenta
`errors` com o detalhe por campo e não passa pelo `error_response` — por isso ele não traz
`request_id`. `schemas/error_schema.py` documenta o envelope inteiro, `errors` incluído, e
`tests/test_contracts.py` reprova se o schema e o código divergirem.

### Segurança das rotas

A exigência de token é declarada **globalmente** no spec OpenAPI (`app/ext/api.py`) e
aplicada por classe (`decorators = [permission_required(...)]`, que implica o token). Rota
que qualquer autenticado acessa usa `authenticated` — o `permission_required()` sem
argumentos, nomeado em `app/ext/auth.py`. Rotas públicas abrem mão explicitamente com
`@blp.doc(security=PUBLIC)`.

Renovar e encerrar a sessão aceitam só o token de renovação: `refresh_token_required`, em
`app/ext/auth.py`, confere a assinatura, a validade e o usuário, e o `TokenService` decide o
que o token ainda vale, na mesma transação da troca. Nas demais rotas, ele é recusado.

A direção é deliberada: **esquecer a anotação fecha a rota, não abre** — um método novo
sem decorator herda o piso da classe. E esquecer de declarar a permissão reprova em
`tests/test_contracts.py`, que varre as rotas registradas atrás de operação protegida sem
permissão declarada. O mesmo arquivo guarda a lista das rotas públicas (`ROTAS_PUBLICAS`):
abrir uma rota exige acrescentá-la ali, então abrir sem querer também reprova.

O limite dessas redes: o piso é de **leitura**. Um método de escrita que esquece o seu
`@permission_required` herda só o piso — fica aberto a quem apenas lê — e o contrato não
percebe, porque aceita o piso como declaração. Quem pega é o teste do papel que só lê em
cada operação de escrita (receita 1.10 de `03-como-estender.md`).

---

## Mapa de arquivos

```
app/
  app.py                     create_app() — não conhece nenhuma extensão
  instrumentation/
    probes.py                registro das sondas do health
    request_context.py       id da requisição
  messaging/                 RabbitMQ
    connection.py            parâmetros de conexão (pika) + sonda do broker
    management.py            cliente da API de gerenciamento (vhosts, permissões, filas, policies)
    topology.py              exchanges, fila, DLX, DLQ, bindings e a policy (aplicados pelo setup)
    publisher.py             publicação dos lotes com confirmação (porta TelemetryPublisher)
    consumer.py              consumo da fila: prefetch, ack manual, DLQ, pausa com backoff
  ext/
    config.py                Dynaconf + perfil do processo + carregador dinâmico de módulos
    database.py              instância do SQLAlchemy + sonda do banco
    mongo.py                 cliente do Mongo (sem conexão até o primeiro uso; um por processo)
                             + sonda + `flask mongo setup` (os índices do bronze)
    rabbitmq.py              configuração AMQP validada na subida + sonda + `flask rabbitmq setup`
    container.py             container de injeção de dependência dos processos HTTP
    consumer_container.py    container do consumer (app.consumer_container)
    consumer.py              `flask consumer run`, que para com SIGTERM ou Ctrl+C
    api.py                   flask-smorest + blueprint /api/v1 + segurança global
    auth.py                  JWT, decorator de permissão, escopo de ownership
    observability.py         request id + nível de log
    cors.py                  origens permitidas
    error_handler.py         registra os handlers a partir do mapa
    commands.py              comandos `flask ...`
  domain/                    o ORM (base, models) e as folhas (o resto)
    base.py                  BaseModel (campos de controle, convenção de nomes)
    models.py                User, Vehicle, VehicleDriver, RefreshToken
    authz.py                 papéis, permissões, o mapa entre eles e o escopo de dono
                             (ANY_OWNER) (folha)
    vin.py                   formato e forma canônica do VIN (folha)
    ports.py                 portas do pipeline: Protocol e os tipos que trocam (folha)
    contracts/
      telemetry_batch.py     contrato da fila v1: envelope, encode/decode e o endereço (folha)
  repository/                uma subpasta por tecnologia
    sql/                     SQLAlchemy: a única parte que monta consulta SQL
      base_repository.py     CRUD genérico
      user_repository.py     uma linha: amarra o modelo
      refresh_token_repository.py  a família de tokens: trava a linha na troca e revoga em massa
      vehicle_repository.py  + authorized_vins: a posse para o envio, de gestor ou de
                             motorista (uma consulta por lote)
      vehicle_driver_repository.py  os vínculos de motorista (joinedload das três relações);
                             desliga os de um veículo excluído
    mongo/                   pymongo: o bronze
      bronze_document.py     BronzeEntry ↔ documento: o único lugar que conhece o layout
      server.py              o prazo de cada operação, a falha da infraestrutura
                             (BronzeUnavailable) e a hora do servidor: comum ao writer e ao leitor
      bronze_writer.py       gravação idempotente pelo _id (porta BronzeWriter) + hora do servidor
      bronze_reader.py       leitura em ordem de gravação, depois de uma posição (porta
                             BronzeReader) + hora do servidor
      indexes.py             os índices do bronze (aplicados pelo setup)
  service/
    base_service.py          CRUD + ownership + ganchos
    errors.py                exceções de domínio
    pagination.py            dataclasses Page (por número de página) e CursorPage (por cursor)
    auth_service.py          verificação de credenciais
    user_service.py          cadastro, papel e troca de senha (que encerra as sessões)
    token_service.py         token de renovação: registro, rotação e revogação da família
    vehicle_service.py       VIN na forma canônica e imutável; a exclusão desliga os motoristas
    vehicle_driver_service.py  motoristas: a posse herdada do veículo e a visão do motorista
    telemetry/               o pipeline: só portas e folhas, sem BaseService
      ingest_rules.py        producer: partição por posse, regras puras
      ingest_service.py      producer: posse do lote + publicação; o recibo (IngestReceipt)
      bronze_rules.py        consumer: o BronzeEntry de cada registro, available_metrics
      bronze_ingest_service.py  consumer: grava o lote no bronze; duplicado é sucesso
      bronze_read_service.py    silver: a janela de estabilização, o cursor e o has_more
  schemas/
    pagination_schema.py     parâmetros e envelope de paginação
    error_schema.py          envelope de erro (documentação)
    health_schema.py
    auth_schema.py           login, cadastro, o par de tokens e o usuário
    vehicle_schema.py
    vehicle_driver_schema.py o motorista visto pelo gestor, e o veículo visto pelo motorista
    telemetry_schema.py      o lote do app e o recibo 202
    bronze_schema.py         a query da leitura, o codec do cursor e a página da silver
  blueprint/
    http_errors.py           envelope de erro + mapa exceção → HTTP
  blueprint/restapi/v1/
    health.py                prontidão por dependência e vida (/health/live), públicas
    auth.py                  o par de tokens (login, renovação, logout) e a identidade
    registration.py          POST /auth/register: o cadastro público, só no processo api
    vehicle.py               cadastro de veículos (quem cadastra é o gestor)
    vehicle_driver.py        /vehicle/<id>/drivers e /vehicle/driving: os motoristas
    telemetry.py             POST /telemetry/batches: o lote do app, a caminho da fila
    bronze.py                GET /bronze/records: a silver lê o bronze por cursor

migrations/                  Alembic
tests/                       pytest; a raiz usa Postgres e HTTP, unit/ não usa nada, mongo/ e
                             rabbitmq/ usam o serviço do nome, e e2e/ usa os três; data/
                             guarda o exemplo canônico do contrato da fila
docker/                      entrypoint.sh, setup.sh (o job de preparação: migrações, topologia
                             e índices) e uma pasta por
                             serviço de terceiros (postgres/initdb/)
settings.toml                configuração (sem segredos), as duas listas de módulos e um perfil
                             por processo
requirements.lock.txt        versões exatas do build (versionado)
```

> **Invariante do layout:** `app/` é a raiz do `sys.path` — os imports são
> `from ext import ...`, não `from app.ext import ...`. Esse fato está codificado em cinco
> lugares: `pythonpath` do pytest e `src` do ruff (`pyproject.toml`), `PYTHONPATH` do
> `Dockerfile`, `sys.path.insert` do `migrations/env.py`, e `python.analysis.extraPaths`
> do `.vscode/settings.json`. Mudar o layout exige atualizar os cinco.
