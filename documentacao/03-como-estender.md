# Como estender

Treze receitas, da mais comum para a mais rara. Em todas, use `Vehicle` como molde: modelo,
repositório, serviço, schemas, rota e testes.

| Quero… | Vá para |
|---|---|
| Uma entidade nova, ponta a ponta | [1](#1-entidade-nova) |
| Um endpoint novo numa entidade que já existe | [2](#2-endpoint-novo) |
| Uma consulta que o CRUD genérico não cobre | [3](#3-consulta-nova) |
| Uma regra de negócio nova | [4](#4-regra-de-negócio) |
| Um tipo de erro novo | [5](#5-erro-de-domínio-novo) |
| Um campo novo num modelo existente | [6](#6-campo-novo) |
| Infraestrutura nova (cache, e-mail, agendador…) | [7](#7-extensão-de-infraestrutura) |
| Um comando `flask ...` | [8](#8-comando-cli) |
| Uma chave de configuração | [9](#9-configuração) |
| Restringir uma rota, ou criar permissão/papel | [10](#10-autorização) |
| Mudar a mensagem que atravessa a fila | [11](#11-contrato-da-fila) |
| Um metadado novo no documento do bronze, um filtro da silver ou um índice | [12](#12-bronze) |
| Um sub-recurso que herda a posse de outro (os motoristas do veículo) | [13](#13-sub-recurso) |

No fim há um [checklist de revisão](#checklist-de-revisão).

---

## 1. Entidade nova

Exemplo: `Product`. Dez arquivos, nenhum deles o `app.py`.

### 1.1 Modelo — `app/domain/models.py`

```python
class Product(BaseModel):
    __tablename__ = "products"

    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    owner: Mapped["User"] = relationship(back_populates="products")
```

`id`, `created_at`, `last_update` e `deleted` vêm do `BaseModel`.

O `back_populates` exige os dois lados. Acrescente o inverso em `User`, ao lado do `vehicles`
que já está lá; sem ele, o SQLAlchemy falha ao configurar os mapeamentos no primeiro uso
dos modelos:

```python
class User(BaseModel):
    ...
    products: Mapped[list["Product"]] = relationship(back_populates="owner")
```

Se o nome precisar ser único por dono, use índice **parcial** — não restrição simples,
senão um registro excluído logicamente bloquearia o nome para sempre:

```python
__table_args__ = (
    Index("uq_products_owner_name_active", "owner_id", "name",
          unique=True, postgresql_where=text("deleted = false")),
)
```

### 1.2 Migração

```bash
docker compose exec api alembic revision --autogenerate -m "adiciona products"
# revise o arquivo gerado em migrations/versions/ antes de aplicar
docker compose exec api alembic upgrade head
```

### 1.3 Repositório — `app/repository/sql/product_repository.py`

```python
from domain.models import Product
from repository.sql.base_repository import BaseRepository


class ProductRepository(BaseRepository[Product]):
    def __init__(self) -> None:
        super().__init__(Product)
```

O `[Product]` não é decoração: é o que faz `get_by_id()` ser tipado como
`Product | None`.

> **Se algum schema de saída aninhar um relacionamento** (`owner = fields.Nested(...)`),
> declare o eager load aqui, ou listar 20 registros dispara 21 consultas em silêncio:
> `loader_options = (joinedload(Product.owner),)`. O `owner` é muitos-para-um, e o
> `joinedload` o traz na própria consulta da página; numa coleção (um-para-muitos), use
> `selectinload` (02-decisoes, "Relação muitos-para-um na saída vai com `joinedload`").

### 1.4 Serviço — `app/service/product_service.py`

```python
from domain.authz import OwnerScope
from domain.models import Product
from repository.sql.product_repository import ProductRepository
from service.base_service import BaseService


class ProductService(BaseService[Product]):
    conflict_message = "Você já tem um produto com esse nome."
    not_found_message = "Produto não encontrado."

    def __init__(self, repository: ProductRepository) -> None:
        super().__init__(repository, owned=True)   # owned=False se for entidade global
```

`owned=True` liga o filtro de posse em todas as leituras e escritas. As duas mensagens
aparecem nas respostas 409 e 404.

### 1.5 Schemas — `app/schemas/product_schema.py`

Cinco schemas, cada um com um papel:

```python
#: Os limites valem na criação e na atualização, então ficam num lugar só. Precisam
#: acompanhar o `String(...)` da coluna: divergir dá DataError (500) em vez de 422.
NOME = validate.Length(min=1, max=100)


class ProductSchema(Schema):                       # saída
    id = fields.Int()
    name = fields.Str()
    owner_id = fields.Int()
    created_at = fields.DateTime()

class ProductCreateSchema(Schema):                 # entrada do POST
    name = fields.Str(required=True, validate=NOME)

class ProductUpdateSchema(Schema):                 # entrada do PATCH (ao menos um campo)
    name = fields.Str(validate=NOME)

    @validates_schema
    def _require_at_least_one_field(self, data: dict, **_kwargs) -> None:
        if not data:
            raise ValidationError("Informe ao menos um campo para atualizar.")

class ProductQuerySchema(PaginationQuerySchema):   # filtros da listagem
    name = fields.Str(load_default=None)

class ProductPageSchema(PageSchema):               # envelope da listagem
    items = fields.List(fields.Nested(ProductSchema))
```

O schema é a **única** fonte de verdade da saída — campo que não está aqui não sai. E o de
saída não leva `dump_only`: ele nunca é usado em `@blp.arguments`, então marcar campo como
"só saída" não muda nada e sugere que o schema também serve de entrada.

O validador do `ProductUpdateSchema` faz um PATCH vazio responder 422, em vez de um 200 que
não altera nada.

### 1.6 Container — `app/ext/container.py`

Duas linhas:

```python
product_repository = providers.Factory(ProductRepository)
product_service = providers.Factory(ProductService, repository=product_repository)
```

Adaptador que guarda uma conexão (o `RabbitTelemetryPublisher`) é `providers.Singleton`: uma
instância por processo, que abre a conexão no primeiro uso. Os valores do `settings.toml` que
ele recebe no construtor saem de `config`, um `providers.Configuration` que o `init_app`
preenche a partir do `app.config`. O adaptador continua sem ler `current_app`.

### 1.7 Permissões — `app/domain/authz.py`

A rota vai declarar a capacidade que exige, então ela precisa existir primeiro.
Duas edições, no mesmo arquivo:

```python
class Permission(StrEnum):
    ...
    PRODUCT_LER = "product:ler"
    PRODUCT_ESCREVER = "product:escrever"
    PRODUCT_LER_DE_TODOS = "product:ler-de-todos"
    PRODUCT_ESCREVER_DE_TODOS = "product:escrever-de-todos"

_USUARIO = frozenset({..., Permission.PRODUCT_LER, Permission.PRODUCT_ESCREVER})
_ADMIN = _USUARIO | {..., Permission.PRODUCT_LER_DE_TODOS, Permission.PRODUCT_ESCREVER_DE_TODOS}
```

Os escopos (`..._DE_TODOS`) são o que permite um papel ver tudo e alterar só o
próprio. Permissão declarada e não concedida a papel nenhum **reprova** em
`tests/test_contracts.py` — a rota que a exigisse ficaria inacessível para todo
mundo. Papel novo e outros detalhes: [receita 10](#10-autorização).

### 1.8 Rota — `app/blueprint/restapi/v1/product.py`

```python
blp = Blueprint("product", __name__, url_prefix="/product", description="CRUD de produtos")


class ProductView(MethodView):
    """Base do recurso: declara o piso de permissão e injeta o serviço."""

    decorators = [permission_required(Permission.PRODUCT_LER)]

    @inject
    def __init__(self, product_service: ProductService = Provide[Container.product_service]) -> None:
        self.product_service = product_service


@blp.route("/")
class ProductCollection(ProductView):
    @blp.arguments(ProductQuerySchema, location="query")
    @blp.response(200, ProductPageSchema)
    @error_responses(blp, 403)
    def get(self, query: dict):
        """Lista os produtos do usuário — ou os de todos, para quem tiver a
        permissão de escopo."""
        return self.product_service.get_page(
            current_owner_scope(Permission.PRODUCT_LER_DE_TODOS),
            page=query["page"],
            per_page=query["per_page"],
            like={"name": query["name"]},
        )

    @permission_required(Permission.PRODUCT_ESCREVER)
    @blp.arguments(ProductCreateSchema)
    @blp.response(201, ProductSchema)
    @error_responses(blp, 403, 409)
    def post(self, payload: dict):
        """Cria um produto para o usuário autenticado."""
        # current_user.id, não current_owner_scope(...): criar exige dono concreto.
        return self.product_service.create(current_user.id, **payload)
```

Três coisas a notar:

- `decorators` na classe base vale para **todos** os métodos: é o piso do recurso. Só os
  métodos que exigem mais acrescentam o seu `@permission_required`.
- Não há `try/except`: o serviço levanta exceção de domínio e o handler central traduz.
- `@error_responses(blp, 403, 409)` documenta as respostas de erro no OpenAPI em uma linha.

Para o recurso de um registro só (`/product/<int:product_id>`), copie `VehicleResource` de
`app/blueprint/restapi/v1/vehicle.py`. Atenção à ordem dos parâmetros: o payload do
`@blp.arguments` vem **antes** do parâmetro da URL —
`def patch(self, payload, product_id)`.

### 1.9 Ativar — `settings.toml`

A linha entra em duas listas: a do `[default]`, que a suíte e o desenvolvimento local usam, e a
do perfil do processo que serve a rota. As rotas de negócio ficam no `[api]`.

```toml
[default]
API_V1_ROUTES = [
    "blueprint.restapi.v1.health",
    "blueprint.restapi.v1.auth",
    "blueprint.restapi.v1.vehicle",
    "blueprint.restapi.v1.product",   # ← aqui
]

[api]
API_V1_ROUTES = [
    "blueprint.restapi.v1.health",
    "blueprint.restapi.v1.auth",
    "blueprint.restapi.v1.vehicle",
    "blueprint.restapi.v1.product",   # ← e aqui
]
```

Esquecer qualquer uma das duas reprova em `tests/unit/test_composicao.py`, que exige o
`[default]` igual à união dos perfis. Ele não diz se o perfil é o certo: para isso, a rota
ganha as suas linhas no `/smoke-api`, que sobe o perfil `api`.

### 1.10 Testes — `tests/test_product.py`

Copie `tests/test_vehicle.py`. As fixtures prontas são `client`, `login`, `headers`,
`make_user`, `set_role` e `sql_log` — `login` e `make_user` aceitam `role=Role.ADMIN`, e
`set_role(nome, papel)` troca o papel de quem já existe, sem reemitir o token.

Cubra pelo menos: criação, listagem paginada, conflito de nome duplicado, exclusão lógica
sumindo das consultas, e **recurso de outro usuário respondendo 404**. Cubra também o **403**
em **cada** operação de escrita, com a `silver`, que não tem nenhuma permissão de negócio: na
criação, entre com `login(role=Role.SILVER)`; nas escritas sobre um registro que já existe
(PATCH, DELETE), crie como `usuario` e rebaixe com `set_role(nome, Role.SILVER)` (molde em
`tests/test_vehicle.py`). Esse 403 sai do piso da classe. O `@permission_required` de escrita
esquecido reprova em `test_toda_escrita_protegida_declara_a_propria_permissao`
(`tests/test_contracts.py`), que só confere que o método declara *uma* permissão. Quem prova que
é a certa é um teste que restringe o mapa ao papel que só lê, com `monkeypatch.setitem` em
`ROLE_PERMISSIONS` (molde: `test_quem_so_le_o_veiculo_nao_escreve_nada`, em `tests/test_authz.py`).

---

## 2. Endpoint novo

Numa entidade que já existe, acrescente um método à classe de rota:

```python
@blp.route("/<int:vehicle_id>/arquivar")
class VehicleArquivar(VehicleView):    # herda decorators e o serviço injetado
    @permission_required(Permission.VEICULO_ESCREVER)   # arquivar é escrita
    @blp.response(200, VehicleSchema)
    @error_responses(blp, 403, 404, 422)
    def post(self, vehicle_id: int):
        """Arquiva um veículo."""
        return self.vehicle_service.arquivar(
            vehicle_id, current_owner_scope(Permission.VEICULO_ESCREVER_DE_TODOS)
        )
```

A lógica do `arquivar` vai no **serviço**, não aqui. O endpoint herda o piso de permissão
da classe base; como é escrita, acrescenta a sua — ver a [receita 10](#10-autorização). E
entra no teste do papel que só lê (como as escritas da receita 1.10): sem ele, declarar a
permissão errada passa no CI.

---

## 3. Consulta nova

Consulta que o CRUD genérico não cobre entra como método no **repositório** (em
`app/repository/sql/`), nunca no serviço:

```python
class VehicleRepository(BaseRepository[Vehicle]):
    def contar_por_dono(self, owner_id: int) -> int:
        stmt = db.select(func.count()).select_from(self.model).where(
            self.model.owner_id == owner_id,
            self.model.deleted.is_(False),
        )
        return db.session.execute(stmt).scalar_one()
```

Use `self._select()` como ponto de partida quando quiser o filtro de exclusão lógica e os
`loader_options` já aplicados.

---

## 4. Regra de negócio

Sobrescreva um gancho no serviço. Três disponíveis:

```python
class ProductService(BaseService[Product]):
    def before_create(self, data: dict) -> None:
        """Dados já validados pelo schema, antes de instanciar o modelo."""
        data["name"] = self._clean_text(data.get("name"), "nome do produto")

    def before_update(self, model: Product, changes: dict) -> None:
        """Modelo carregado e os campos que vão mudar."""

    def before_delete(self, model: Product) -> None:
        """Levante para bloquear a exclusão."""
        if model.tem_pedidos_ativos:
            raise BusinessRuleError("Produto com pedidos ativos não pode ser excluído.")
```

`_clean_text` vem do `BaseService`: apara as pontas e levanta `BusinessRuleError` se
sobrar vazio. Está lá, e não copiado em cada serviço, porque "este texto não pode ser
vazio" é a regra mais repetida que existe — e o porquê de ela viver no serviço, e não só no
schema, está em [02-decisoes.md](02-decisoes.md).

Para uma operação que não é CRUD, adicione um método novo à classe — recebendo o escopo de
dono e chamando `self.get_by_id(id, owner_scope)` para carregar com a posse validada.

---

## 5. Erro de domínio novo

Dois passos, nessa ordem:

**1.** A classe em `app/service/errors.py`:

```python
class RateLimitError(ServiceError):
    """Excedeu o limite de requisições."""
```

**2.** A linha no mapa, em `app/blueprint/http_errors.py`:

```python
ERROR_STATUS = {
    ...
    RateLimitError: 429,
}
```

Se o status ainda não estiver em `STATUS_DESCRIPTION`, acrescente também.

Erro que pede um header na resposta é a exceção, e não a rota, que o carrega. O
`ServiceUnavailableError` leva `retry_after`, e o `ext/error_handler.py` o transforma em
`Retry-After`. O serviço continua sem saber o que é header: ele diz quando repetir, e a camada
HTTP decide como contar isso ao cliente.

Esquecer o passo 2 **reprova** em `tests/test_contracts.py` — sem esse teste, a exceção
cairia no handler genérico e viraria um 500 silencioso.

---

## 6. Campo novo

```python
# 1. app/domain/models.py
quantidade: Mapped[int] = mapped_column(default=0, server_default=text("0"))
```

```bash
# 2. migração
docker compose exec api alembic revision --autogenerate -m "product.quantidade"
```

Revise o arquivo gerado. **Coluna `NOT NULL` em tabela com dados precisa de
`server_default`**, senão o `ALTER TABLE` falha.

```python
# 3. app/schemas/product_schema.py — o campo só aparece na API se entrar aqui
quantidade = fields.Int()                                      # em ProductSchema (saída)
quantidade = fields.Int(load_default=0, validate=validate.Range(min=0))  # em ProductCreateSchema
```

```bash
# 4. aplicar e conferir
docker compose exec api alembic upgrade head
docker compose exec api alembic check     # deve dizer "No new upgrade operations detected"
```

---

## 7. Extensão de infraestrutura

Para algo transversal — cache, envio de e-mail, agendador, métricas.

```python
# app/ext/cache.py
from flask import Flask
from alguma_lib import Cache

cache = Cache()


def init_app(app: Flask) -> None:
    cache.init_app(app, url=app.config["CACHE_URL"])
```

```toml
# settings.toml: no [default] E no perfil de cada processo que usa a extensão
EXTENSIONS = [
    "ext.observability",
    "ext.database",
    "ext.cache",          # ← aqui
    ...
    "ext.api",            # sempre por último: é quem registra as rotas
]
```

Duas regras:

- **Exatamente uma função `init_app(app)`** por módulo. É a convenção que o carregador
  espera.
- **Nenhuma extensão importa outra extensão.** Se duas precisarem da mesma coisa, essa
  coisa vira um módulo neutro fora de `ext/` — foi exatamente o caso de
  `blueprint/http_errors.py` e de `instrumentation/` — ou, se for vocabulário puro, uma folha
  do domínio (`app/domain/`). As três extensões que já são importadas por
  outras, e por quê, estão nas [regras de dependência](01-arquitetura.md#regras-de-dependência).

A ordem da lista é a ordem de inicialização. `ext.observability` fica primeiro (para que o
resto já logue com request id), `ext.api` por último.

A extensão entra no `[default]` e no perfil de cada processo que a usa, e só nele: o processo
que não a lista nem a importa ([perfis](01-arquitetura.md#um-perfil-por-processo)). O
`tests/unit/test_composicao.py` reprova se o `[default]` deixar de ser a união dos perfis. Se
algum processo nunca puder carregá-la, acrescente-a ao `FORA_DO_PERFIL` desse teste.

**Se ela conecta um serviço externo**, o health do processo precisa saber dele:

```python
# app/ext/cache.py
from instrumentation.probes import register_probe


def _cache_responds() -> bool:
    """Não levanta exceção: a falha é a resposta, registrada no log pela própria sonda."""
    ...


def init_app(app: Flask) -> None:
    ...
    register_probe(app, "cache", _cache_responds)
```

1. A sonda devolve `bool` e desiste antes de `HEALTH_PROBE_TIMEOUT_SECONDS`. Dependência lenta
   não pode estourar o timeout do healthcheck do container.
2. O campo `cache` entra no `HealthSchema` (`app/schemas/health_schema.py`). Sem ele, a chave
   sumiria da resposta, e o `tests/test_contracts.py` reprova.
3. Decida se ela é **crítica**. Se o processo não serve nada sem ela, acrescente-a ao
   `HEALTH_CRITICAL` do perfil (a queda passa a responder 503 e tira o processo do
   balanceador). Se não, ela aparece como `unreachable`, com `status` `degraded` e 200.
4. A configuração obrigatória falha no `init_app`, com mensagem que nunca repete a URL, nem em
   pedaços (ela leva a senha). O erro que a biblioteca dá ao ler a URL repete trechos dela:
   troque-o por uma mensagem fixa, com `from None`, para que ele saia também do traceback. Os
   moldes são `ext/mongo.py` e `messaging/connection.py`.

---

## 8. Comando CLI

```python
# app/ext/commands.py
@click.command("recalcular-totais")
@with_appcontext
def recalcular_totais() -> None:
    """Recalcula os totais de todos os produtos."""
    service = current_app.container.product_service()
    ...
    click.echo("Pronto.")


def init_app(app: Flask) -> None:
    for command in (create_db, drop_db, ..., prune_tokens, recalcular_totais):  # ← aqui
        app.cli.add_command(command)
```

`@with_appcontext` é obrigatório para qualquer comando que toque no banco. Os serviços
saem do container (`current_app.container.<nome>_service()`) — o comando não alcança
repositório nem sessão, pelo mesmo motivo que a rota não alcança. Erro de domínio vira
mensagem de terminal com `raise click.ClickException(str(error)) from error`, como em
`create-user` e `set-role`.

Uso: `docker compose exec api flask recalcular-totais`

**Comando de operação de uma infraestrutura** (aplicar a topologia do RabbitMQ, criar os índices
do Mongo) não vai em `ext/commands.py`: fica na extensão dona daquela infraestrutura, num
`AppGroup` com o nome do módulo, registrado no `init_app` dela. Os moldes são o
`flask rabbitmq setup` de `ext/rabbitmq.py` e o `flask mongo setup` de `ext/mongo.py`:

```python
rabbitmq_cli = AppGroup("rabbitmq", help="Operação do RabbitMQ.")


@rabbitmq_cli.command("setup")
def setup() -> None:
    ...


def init_app(app: Flask) -> None:
    ...
    app.cli.add_command(rabbitmq_cli)
```

O motivo é o perfil. O job `setup` carrega só o que usa (`[setup]`), e o `ext.commands` traz o
banco e o container junto. Na extensão, o comando chega a todo processo que conhece aquela
infraestrutura, e a nenhum outro. A credencial de operação (`AMQP_MANAGEMENT_URL`) é lida só
dentro do comando: o processo que nunca o roda não precisa dela. Erro da infraestrutura vira
`click.ClickException` com a mensagem, sem o traceback.

**Processo de longa duração** (o `flask consumer run`) segue o mesmo formato, numa extensão só
dele (`ext/consumer.py`), que só o perfil daquele processo carrega. O comando pega o objeto
pronto no container do processo e instala o handler de SIGTERM: no container, ele é o PID 1.
Como não tem rota de health, responde a saúde numa porta própria, em
`instrumentation/consumer_health.py` (02-decisoes, "Parada e queda").

---

## 9. Configuração

**Valor não sensível** → `settings.toml`:

```toml
CACHE_TTL = 300
```

**Valor sensível** → nunca no `settings.toml`. Documente em `.env.example`, use prefixo
`FLASK_`, e consuma por `app.config`:

```bash
# .env.example
FLASK_CACHE_URL=redis://localhost:6379/0
```

```python
app.config["CACHE_URL"]        # dentro de um init_app
current_app.config["CACHE_TTL"]  # dentro de uma requisição
```

Se o valor for obrigatório, falhe na inicialização com mensagem explícita — como
`ext/database.py` e `ext/auth.py` fazem. Melhor não subir do que subir quebrado.

**Valor diferente por processo** → declare a chave no `[default]` e repita-a na seção do perfil
que precisa de outro valor, como o `HEALTH_CRITICAL` do `[bronze_api]`. A seção do perfil vale
sobre o `[default]`, e uma lista declarada nela substitui a do `[default]` inteira. Repetir o
valor do `[default]` também vale quando a escolha é do processo e merece ficar escrita nele,
como o `HEALTH_CRITICAL` de cada perfil. Quem escolhe o perfil é a variável `FLASK_PROCESS` do
processo, no compose.

> Se o valor for usado **e** validado por um schema (como os limites de paginação), mantenha
> a constante junto do schema, não na configuração — para não haver duas fontes de verdade.

---

## 10. Autorização

As três coisas que você vai querer fazer, da mais comum para a mais rara. O vocabulário
inteiro mora em `app/domain/authz.py`.

### 10.1 Restringir uma rota

A classe declara o **piso** do recurso; o método que exige mais acrescenta o seu.

```python
# app/blueprint/restapi/v1/product.py
from domain.authz import Permission
from ext.auth import current_owner_scope, permission_required


class ProductView(MethodView):
    decorators = [permission_required(Permission.PRODUCT_LER)]

    ...


@blp.route("/")
class ProductCollection(ProductView):
    @blp.arguments(ProductQuerySchema, location="query")
    @blp.response(200, ProductPageSchema)
    @error_responses(blp, 403)
    def get(self, query: dict):                       # só o piso
        return self.product_service.get_page(
            current_owner_scope(Permission.PRODUCT_LER_DE_TODOS),
            page=query["page"],
            per_page=query["per_page"],
            like={"name": query["name"]},
        )

    @permission_required(Permission.PRODUCT_ESCREVER)  # exige mais que o piso
    @blp.arguments(ProductCreateSchema)
    @blp.response(201, ProductSchema)
    @error_responses(blp, 403, 409)
    def post(self, payload: dict):
        return self.product_service.create(current_user.id, **payload)
```

Três detalhes que importam:

- **`@permission_required` vem antes dos decorators do smorest.** Quem não pode fazer a
  operação deve levar 403 sem antes descobrir o formato do corpo dela num 422.
- **Rota que qualquer autenticado acessa declara isso**: `decorators = [authenticated]`,
  que é o `permission_required()` sem argumentos com um nome legível. É o caso de
  `/auth/me`.
- **Não escreva `if`.** Se você sentiu vontade de verificar papel dentro da rota ou do
  serviço, a permissão que falta é que ainda não existe — crie-a (10.2).

### 10.2 Permissão nova

Duas linhas, nessa ordem:

1. O membro em `Permission` (`app/domain/authz.py`), no formato `recurso:verbo`.
2. A concessão em `ROLE_PERMISSIONS`, a pelo menos um papel.

```python
class Permission(StrEnum):
    ...
    PRODUCT_ESCREVER = "product:escrever"

_USUARIO = frozenset({..., Permission.PRODUCT_ESCREVER})
```

Esquecer o passo 2 **reprova** em `tests/test_contracts.py`: a permissão órfã deixaria a
rota inacessível para todo mundo, sem erro nenhum que apontasse para cá.

> Escopo de leitura e de escrita são permissões **separadas** (`..._LER_DE_TODOS`,
> `..._ESCREVER_DE_TODOS`). É isso que permite o papel que enxerga tudo e altera só o
> próprio. Uma só permissão de escopo usada no `DELETE` seria um nome mentiroso.

### 10.3 Papel novo

Quatro passos — o terceiro é o que costuma ser esquecido, porque é o único que toca o
banco.

1. O membro em `Role` (`app/domain/authz.py`).
2. A entrada em `ROLE_PERMISSIONS`, de preferência a partir de um papel existente
   (`_REVISOR = _USUARIO | {Permission.VEICULO_LER_DE_TODOS}`). Papel que não estende nenhum
   (uma conta de sistema, como a `silver`) é um ramo à parte, com o próprio `frozenset`.
3. **A migração que troca a restrição `CHECK`**, porque o banco recusa um valor que não
   está nela:

   ```python
   # migrations/versions/000X_papel_revisor.py  (molde real: 0002_papel_silver.py)
   PAPEIS = ("usuario", "revisor", "admin", "silver")

   def upgrade() -> None:
       valores = ", ".join(f"'{p}'" for p in PAPEIS)
       op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
       op.create_check_constraint(op.f("ck_users_role"), "users", f"role IN ({valores})")
   ```

   > Nome de restrição em migração se escreve completo, dentro de `op.f()` — que
   > significa "este nome já é final". Sem ele, a convenção do projeto
   > (`app/domain/base.py`) seria aplicada por cima do que você passou.

4. `alembic upgrade head` e `alembic check`.

O `--role` da CLI sai da própria enumeração, então não há segunda lista para atualizar.
Esquecer o passo 2 **reprova** em `tests/test_contracts.py`.

## 11. Contrato da fila

O contrato `telemetry.batch.v1` mora em `app/domain/contracts/telemetry_batch.py`: o envelope
(dataclasses), o `encode`/`decode` e o endereço (`EXCHANGE`, `ROUTING_KEY`). O producer e o
consumer só se conhecem por ele (02-decisoes, "O contrato da fila é uma folha versionada").

### 11.1 Campo novo e opcional — a mesma versão

O decoder ignora campo desconhecido, então o consumer antigo segue funcionando.

1. O campo na dataclass, **com valor padrão**, e no schema privado do mesmo módulo, com
   `load_default`, para o decoder aceitar a mensagem que ainda não o traz:

   ```python
   @dataclass(frozen=True, slots=True)
   class TelemetryRecordV1:
       ...
       app_version: str | None = None


   class _RecordSchema(Schema):
       ...
       app_version = fields.String(load_default=None, allow_none=True)
   ```

2. O campo no exemplo canônico, `tests/data/telemetry_batch_v1.json`: o encoder passa a
   escrevê-lo, e `test_codificar_o_exemplo_devolve_o_mesmo_json` reprova até o exemplo
   acompanhar.
3. Publique o producer novo. O consumer só passa a **usar** o campo depois disso, e trata a
   ausência: mensagens antigas ainda podem estar na fila.

### 11.2 Mudança incompatível — a v2

Remover, renomear, tornar obrigatório ou mudar o significado de um campo.

1. Um módulo novo, `telemetry_batch_v2.py`, com `SCHEMA_VERSION = 2` e
   `ROUTING_KEY = "telemetry.batch.v2"`, e o exemplo canônico dele em `tests/data/`. O v1 fica
   como está.
2. Em `messaging/topology.py`, um segundo binding da mesma fila para a routing key nova, e
   `flask rabbitmq setup`.
3. O consumer aprende a decodificar as duas versões, pelo `schema_version`, e é publicado
   **primeiro**.
4. Só então o producer passa a publicar a v2. A porta `TelemetryPublisher.publish` recebe
   um `TelemetryBatchV1`, e o `messaging/publisher.py` importa o endereço e o `encode` da v1:
   os dois mudam juntos para a v2, com o serviço que monta o envelope. Quando a fila não tiver
   mais mensagens v1, o v1 e o binding dele podem sair.

### 11.3 Vínculo novo em `SenderRelation`

`test_os_vinculos_da_v1_sao_os_do_dominio` reprova: os valores da v1 estão escritos no
contrato, de propósito. Se o consumer já conhece o valor novo (ou o trata como desconhecido
sem quebrar), acrescente-o a `SENDER_RELATIONS`; senão, é a v2.

---

## 12. Bronze

O bronze é a coleção `telemetry_records` do Mongo, que o consumer grava: um documento por
registro, com `_id` igual ao `id` do registro, o bruto em `raw` e os metadados da ingestão em
`ingestion` (02-decisoes, "A gravação no bronze é idempotente pelo `_id`"). A silver o lê
pelo `bronze-api`, em ordem de gravação, por cursor (02-decisoes, "A silver lê o bronze por
cursor"). Os serviços montam e recebem um `BronzeEntry` e não conhecem o BSON. Só o
`repository/mongo/bronze_document.py` conhece o layout do documento: `to_document` na
gravação, `from_document` na leitura. O que o consumer recusa vai para `telemetry_rejections`
(`BronzeRejection`, `to_rejection_document`).

### 12.1 Metadado novo no documento

1. O campo em `BronzeEntry` (`app/domain/ports.py`), com o tipo do domínio (`UUID`, `datetime`
   com fuso).
2. O valor em `bronze_entry` (`app/service/telemetry/bronze_rules.py`). Ele vem do envelope, do
   registro ou de uma regra pura do mesmo módulo, como `available_metrics`. Se vem da mensagem,
   o contrato precisa trazê-lo antes (receita 11.1).
3. A chave em `to_document` (`app/repository/mongo/bronze_document.py`), dentro de `ingestion`.
   Id vai como texto, e data como `datetime` (o BSON guarda milissegundos). E a volta em
   `from_document`, no mesmo arquivo.
4. Se a silver deve vê-lo, o campo no `BronzeRecordSchema` (`app/schemas/bronze_schema.py`); se
   não, o nome em `OMITIDOS_DA_SILVER` (`tests/test_contracts.py`). O teste reprova o campo novo
   que não esteja em nenhum dos dois, e o do schema que não exista no `BronzeEntry`.
5. Os testes: a regra em `tests/unit/test_gravacao_bronze.py`; a ida e a volta em
   `test_o_documento_lido_devolve_o_registro_gravado` (`tests/unit/test_leitura_bronze.py`); e
   o layout em `test_grava_um_documento_por_registro_no_layout_do_bronze` (`tests/mongo/`).

O bronze não é reescrito: os documentos já gravados ficam sem o campo. O campo novo do
`BronzeEntry` leva um valor padrão, e o `from_document` o lê com `ingestion.get(...)`.

### 12.2 Filtro novo na leitura da silver

1. O campo em `BronzeFilters` (`app/domain/ports.py`), com valor padrão: campo vazio não restringe.
2. O parâmetro em `BronzeQuerySchema` (`app/schemas/bronze_schema.py`), montado em
   `_monta_a_leitura` na forma canônica, e o campo em `filters_fingerprint`. Sem ele, trocar esse
   filtro no meio da leitura não seria notado.
3. A condição em `page_query` (`app/repository/mongo/bronze_reader.py`) e no `LeituraEmMemoria`
   (`tests/conftest.py`).
4. Se o filtro corta bastante, um índice com a ordem de leitura depois dele (receita 12.3) e o caso
   em `test_a_pagina_filtrada_sai_do_indice_sem_ordenar_em_memoria`. Se é só um filtro residual,
   diga na descrição do parâmetro com o que combiná-lo.
5. Os testes: `test_filtrando_le_tudo_uma_vez_so_mesmo_com_empate_no_milissegundo` (`tests/mongo/`)
   e o `test_o_cursor_de_outro_filtro_e_recusado` (`tests/unit/test_leitura_bronze.py`).

A impressão é posicional: um campo novo em `filters_fingerprint` muda a de todo cursor já emitido,
mesmo sem o filtro novo. A silver levaria 422 com o cursor guardado. Por isso o filtro novo sobe
`CURSOR_VERSION`, e a versão anterior continua em `_CURSOR_KEYS`, com a impressão no formato antigo.

### 12.3 Índice novo

1. Uma constante com o nome (`ix_<campos>`) e o `create_index` em `apply`
   (`app/repository/mongo/indexes.py`), com o nome na lista que ela devolve.
2. A definição no `test_os_indices_do_bronze_se_aplicam_de_novo_sem_mudar_nada`
   (`tests/mongo/`): o teste já confere que todo nome devolvido existe, mas só confere a chave
   do índice que tem a asserção.
3. Aplicar: o job `setup` roda o `flask mongo setup` a cada `docker compose up`.

O `create_index` é idempotente, mas mudar a definição de um índice que já existe reprova a
aplicação: o Mongo recusa outro índice com o mesmo nome. Crie o novo com outro nome e apague o
antigo numa mudança à parte. Índice que a consulta não usa é custo em toda gravação: meça
antes com o `explain()`. O índice de uma leitura da silver ganha um teste do plano, no molde do
`test_a_pagina_sai_do_indice_sem_ordenar_em_memoria` (`tests/mongo/test_bronze_reader.py`).

---

## 13. Sub-recurso

Exemplo: os motoristas do veículo (`VehicleDriver`). O sub-recurso não tem dono próprio: quem
pode mexer nele é quem pode mexer no pai (02-decisoes, "O sub-recurso herda a posse do pai pelo
serviço do pai"). Molde: os arquivos `vehicle_driver_*`, a rota `vehicle_driver.py` e
`tests/test_vehicle_driver.py`.

1. **Modelo e migração** (1.1 e 1.2): a FK para o pai, com `ondelete="CASCADE"` e
   `index=True`, e a unicidade entre os ativos num índice parcial
   (`uq_vehicle_drivers_vehicle_driver_active`). Os dois lados de cada `relationship`.
2. **Repositório:** as relações que os schemas de saída mostram entram em `loader_options`:
   `joinedload` para muitos-para-um, `selectinload` para coleção. A exclusão em massa (os
   filhos do pai excluído) é um `UPDATE` só, sem commit (`soft_delete_by_vehicle`).
3. **Serviço:** `owned=False`, com o **serviço do pai** no construtor. Toda operação do dono
   do pai (as do gestor) começa carregando o pai com o escopo recebido:

   ```python
   class VehicleDriverService(BaseService[VehicleDriver]):
       def __init__(self, repository, *, vehicles: VehicleService, users: UserRepository): ...

       def remove_driver(self, vehicle_id: int, driver_id: int, owner_scope: OwnerScope):
           vehicle = self.vehicles.get_by_id(vehicle_id, owner_scope)  # 404 do pai alheio
           link = self.repository.get_active(vehicle.id, driver_id)
           ...
   ```

   A visão da outra ponta do vínculo (`list_driving`, `leave`) não passa pelo pai: recebe o
   id do motorista (`driver_id: int`, vindo do token) e filtra por ele, sem versão "de todos".
   O serviço do pai recebe só o repositório do filho e desliga os filhos no `before_delete`: o
   `delete` faz o commit das duas escritas juntas.
4. **Container:** o provider do filho recebe o do pai (`vehicles=vehicle_service`), e o do pai,
   o repositório do filho (`drivers=vehicle_driver_repository`).
5. **Schemas:** a saída lê a relação pelo `attribute` (`fields.Str(attribute="driver.name")`).
   O vínculo que só nasce e termina não tem PATCH; a lista usa o `PaginationQuerySchema` como
   está.
6. **Rota:** um módulo próprio, com o `url_prefix` do pai (`/vehicle`) e outro nome de
   blueprint. As permissões e os escopos são os do pai (`VEICULO_LER` no piso,
   `VEICULO_ESCREVER` em cada escrita), inclusive na criação do filho, que passa
   `current_owner_scope(Permission.VEICULO_ESCREVER_DE_TODOS)`: o filho nasce num pai que já
   tem dono. A visão da outra ponta do vínculo (`/vehicle/driving`, a do motorista) passa
   `current_user.id`.
7. **Ativar** como na 1.9 e **testar** como na 1.10, mais: o 404 do pai de outro dono em
   **cada** operação do filho; um vínculo de outro par (outro pai, outra pessoa) que nenhuma
   operação alcança; o filho que some quando o pai é excluído; e o custo da listagem,
   com filhos de pessoas diferentes, em `tests/test_read_cost.py` (medido com o
   `/medir-consultas`).

---

## Checklist de revisão

Antes de abrir o PR:

```bash
docker compose run --rm test           # testes
ruff check . && ruff format .          # lint e formatação
mypy                                   # tipos
docker compose exec api alembic check  # migração em sincronia com os modelos
```

E confira:

- [ ] A rota não tem `try/except` nem monta consulta.
- [ ] O serviço não sabe o que é HTTP (nenhum status, header ou `request`).
- [ ] O SQL está só no repositório.
- [ ] Todo método de serviço que depende de posse recebe o escopo de dono explicitamente. O
      de um sub-recurso carrega o pai com esse escopo antes de tocar no filho (receita 13).
- [ ] Rota nova está protegida — ou é pública **de propósito**, marcada com
      `@blp.doc(security=PUBLIC)` **e** acrescentada a `ROTAS_PUBLICAS`, em
      `tests/test_contracts.py`. O teste compara essa lista com o que o spec declara
      aberto, então abrir uma rota sem fazer as duas coisas reprova no CI.
- [ ] Rota nova declara a **permissão** que exige, e não só o token. (Esquecer reprova em
      `tests/test_contracts.py`, mas é melhor descobrir antes.) Cada método de escrita
      declara a sua no próprio método: o piso de leitura da classe não conta para a escrita,
      e o contrato reprova sem ela. O teste de 403 prova que a permissão é a certa.
- [ ] Módulo novo de `EXTENSIONS` ou de `API_V1_ROUTES` está no `[default]` **e** no perfil de
      cada processo que o usa. Esquecer uma das duas reprova em `tests/unit/test_composicao.py`;
      pôr no perfil errado, não.
- [ ] Dependência externa nova registra a sua sonda, tem campo no `HealthSchema` e está, ou
      não está, no `HEALTH_CRITICAL` de cada perfil por decisão consciente.
- [ ] Campo novo aparece na API só se estiver no schema.
- [ ] Mudou o contrato da fila? Campo opcional novo vai com valor padrão e no exemplo
      canônico; qualquer outra mudança é a v2 (receita 11).
- [ ] O pipeline (`service/telemetry/`) só recebe adaptadores pelo construtor e não importa
      infraestrutura (`tests/unit/test_fronteiras.py` reprova).
- [ ] Producer e consumer só se ligam pela fila: nenhum módulo do consumer alcança o lado HTTP
      ou o SQL, e nenhum do producer alcança o Mongo. A leitura da silver não alcança a fila
      nem a gravação, e o consumer não alcança a leitura (`tests/unit/test_fronteiras.py`
      reprova).
- [ ] O consumer só confirma a mensagem (`basic_ack`) depois da gravação confirmada. Falha
      transitória volta com `nack`, bug com `reject` com requeue, e só a mensagem fora do
      contrato vai direto para a DLQ (02-decisoes, "O destino de cada mensagem").
- [ ] Teste cobrindo acesso de outro usuário (deve ser **404**) e, se a rota exige uma
      permissão que algum papel não tem, acesso sem ela em cada operação (deve ser **403** —
      `login(role=Role.SILVER)` na criação; nas escritas sobre registro existente, rebaixe o
      dono com `set_role`), e o papel que só lê em cada escrita (`tests/test_authz.py`).
- [ ] Nenhum segredo em arquivo versionado.
