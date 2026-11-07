# Como estender

Dez receitas, da mais comum para a mais rara. Em todas, use `Item` como molde — a
entidade de exemplo existe para ser copiada (e removida quando você tiver as suas).

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

O `back_populates` exige os dois lados. Acrescente o inverso em `User`, ao lado do `items`
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

### 1.3 Repositório — `app/repository/product_repository.py`

```python
from domain.models import Product
from repository.base_repository import BaseRepository


class ProductRepository(BaseRepository[Product]):
    def __init__(self) -> None:
        super().__init__(Product)
```

O `[Product]` não é decoração: é o que faz `get_by_id()` ser tipado como
`Product | None`.

> **Se algum schema de saída aninhar um relacionamento** (`owner = fields.Nested(...)`),
> declare o eager load aqui, ou listar 20 registros dispara 21 consultas em silêncio:
> `loader_options = (selectinload(Product.owner),)`

### 1.4 Serviço — `app/service/product_service.py`

```python
from domain.models import Product
from repository.product_repository import ProductRepository
from service.base_service import BaseService, OwnerScope


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

### 1.7 Permissões — `app/authz.py`

A rota vai declarar a capacidade que exige, então ela precisa existir primeiro.
Duas edições, no mesmo arquivo:

```python
class Permission(StrEnum):
    ...
    PRODUCT_LER = "product:ler"
    PRODUCT_ESCREVER = "product:escrever"
    PRODUCT_LER_DE_TODOS = "product:ler-de-todos"
    PRODUCT_ESCREVER_DE_TODOS = "product:escrever-de-todos"

_LEITOR = frozenset({..., Permission.PRODUCT_LER})
_USUARIO = _LEITOR | {..., Permission.PRODUCT_ESCREVER}
_GERENTE = _USUARIO | {..., Permission.PRODUCT_LER_DE_TODOS}
_ADMIN = _GERENTE | {..., Permission.PRODUCT_ESCREVER_DE_TODOS}
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

Para o recurso de item único (`/product/<int:product_id>`), copie `ItemResource` de
`app/blueprint/restapi/v1/item.py`. Atenção à ordem dos parâmetros: o payload do
`@blp.arguments` vem **antes** do parâmetro da URL —
`def patch(self, payload, product_id)`.

### 1.9 Ativar — `settings.toml`

```toml
API_V1_ROUTES = [
    "blueprint.restapi.v1.health",
    "blueprint.restapi.v1.auth",
    "blueprint.restapi.v1.item",
    "blueprint.restapi.v1.product",   # ← só isso
]
```

### 1.10 Testes — `tests/test_product.py`

Copie `tests/test_item.py`. As fixtures prontas são `client`, `login`, `headers`,
`make_user`, `set_role` e `sql_log` — `login` e `make_user` aceitam `role=Role.GERENTE`, e
`set_role(nome, papel)` troca o papel de quem já existe, sem reemitir o token.

Cubra pelo menos: criação, listagem paginada, conflito de nome duplicado, exclusão lógica
sumindo das consultas, e **recurso de outro usuário respondendo 404**. Se alguma operação
exigir permissão que algum papel não tem, cubra também o **403** em **cada** operação de
escrita, com o `leitor`, o papel somente-leitura que a base traz. Na criação, entre com
`login(role=Role.LEITOR)`; nas escritas sobre um registro que já existe (PATCH, DELETE), crie
como `usuario` e rebaixe com `set_role(nome, Role.LEITOR)` (molde em `tests/test_authz.py`).
É o único teste que pega um `@permission_required` de escrita esquecido, porque o piso de
leitura da classe já satisfaz o `tests/test_contracts.py`.

---

## 2. Endpoint novo

Numa entidade que já existe, acrescente um método à classe de rota:

```python
@blp.route("/<int:item_id>/arquivar")
class ItemArquivar(ItemView):          # herda decorators e o serviço injetado
    @permission_required(Permission.ITEM_ESCREVER)   # arquivar é escrita
    @blp.response(200, ItemSchema)
    @error_responses(blp, 403, 404, 422)
    def post(self, item_id: int):
        """Arquiva um item."""
        return self.item_service.arquivar(
            item_id, current_owner_scope(Permission.ITEM_ESCREVER_DE_TODOS)
        )
```

A lógica do `arquivar` vai no **serviço**, não aqui. O endpoint herda o piso de permissão
da classe base; como é escrita, acrescenta a sua — ver a [receita 10](#10-autorização). E
ganha o seu teste de 403: o dono, rebaixado a `leitor` com `set_role`, não arquiva (como nas
escritas da receita 1.10). Sem esse teste, esquecer o decorator passa no CI.

---

## 3. Consulta nova

Consulta que o CRUD genérico não cobre entra como método no **repositório** — nunca no
serviço:

```python
class ItemRepository(BaseRepository[Item]):
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
class ItemService(BaseService[Item]):
    def before_create(self, data: dict) -> None:
        """Dados já validados pelo schema, antes de instanciar o modelo."""
        data["name"] = self._clean_text(data.get("name"), "nome do item")

    def before_update(self, model: Item, changes: dict) -> None:
        """Modelo carregado e os campos que vão mudar."""

    def before_delete(self, model: Item) -> None:
        """Levante para bloquear a exclusão."""
        if model.tem_pedidos_ativos:
            raise BusinessRuleError("Item com pedidos ativos não pode ser excluído.")
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

**2.** A linha no mapa, em `app/http_errors.py`:

```python
ERROR_STATUS = {
    ...
    RateLimitError: 429,
}
```

Se o status ainda não estiver em `STATUS_DESCRIPTION`, acrescente também.

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
docker compose exec api alembic revision --autogenerate -m "item.quantidade"
```

Revise o arquivo gerado. **Coluna `NOT NULL` em tabela com dados precisa de
`server_default`**, senão o `ALTER TABLE` falha.

```python
# 3. app/schemas/item_schema.py — o campo só aparece na API se entrar aqui
quantidade = fields.Int()                                      # em ItemSchema (saída)
quantidade = fields.Int(load_default=0, validate=validate.Range(min=0))  # em ItemCreateSchema
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
# settings.toml
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
  coisa vira um módulo neutro na raiz de `app/` — foi exatamente o caso de
  `http_errors.py`. As duas extensões que já são importadas por outras, e por quê, estão
  nas [regras de dependência](01-arquitetura.md#regras-de-dependência).

A ordem da lista é a ordem de inicialização. `ext.observability` fica primeiro (para que o
resto já logue com request id), `ext.api` por último.

---

## 8. Comando CLI

```python
# app/ext/commands.py
@click.command("recalcular-totais")
@with_appcontext
def recalcular_totais() -> None:
    """Recalcula os totais de todos os itens."""
    service = current_app.container.item_service()
    ...
    click.echo("Pronto.")


def init_app(app: Flask) -> None:
    for command in (create_db, drop_db, create_user, set_role, recalcular_totais):  # ← aqui
        app.cli.add_command(command)
```

`@with_appcontext` é obrigatório para qualquer comando que toque no banco. Os serviços
saem do container (`current_app.container.<nome>_service()`) — o comando não alcança
repositório nem sessão, pelo mesmo motivo que a rota não alcança. Erro de domínio vira
mensagem de terminal com `raise click.ClickException(str(error)) from error`, como em
`create-user` e `set-role`.

Uso: `docker compose exec api flask recalcular-totais`

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

> Se o valor for usado **e** validado por um schema (como os limites de paginação), mantenha
> a constante junto do schema, não na configuração — para não haver duas fontes de verdade.

---

## 10. Autorização

As três coisas que você vai querer fazer, da mais comum para a mais rara. O vocabulário
inteiro mora em `app/authz.py`.

### 10.1 Restringir uma rota

A classe declara o **piso** do recurso; o método que exige mais acrescenta o seu.

```python
# app/blueprint/restapi/v1/product.py
from authz import Permission
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

1. O membro em `Permission` (`app/authz.py`), no formato `recurso:verbo`.
2. A concessão em `ROLE_PERMISSIONS`, a pelo menos um papel.

```python
class Permission(StrEnum):
    ...
    PRODUCT_ESCREVER = "product:escrever"

_USUARIO = _LEITOR | {..., Permission.PRODUCT_ESCREVER}
```

Esquecer o passo 2 **reprova** em `tests/test_contracts.py`: a permissão órfã deixaria a
rota inacessível para todo mundo, sem erro nenhum que apontasse para cá.

> Escopo de leitura e de escrita são permissões **separadas** (`..._LER_DE_TODOS`,
> `..._ESCREVER_DE_TODOS`). É isso que permite o papel que enxerga tudo e altera só o
> próprio. Uma só permissão de escopo usada no `DELETE` seria um nome mentiroso.

### 10.3 Papel novo

Quatro passos — o terceiro é o que costuma ser esquecido, porque é o único que toca o
banco.

1. O membro em `Role` (`app/authz.py`).
2. A entrada em `ROLE_PERMISSIONS`, de preferência a partir de um papel existente
   (`_REVISOR = _USUARIO | {Permission.ITEM_LER_DE_TODOS}`).
3. **A migração que troca a restrição `CHECK`**, porque o banco recusa um valor que não
   está nela:

   ```python
   # migrations/versions/000X_papel_revisor.py
   PAPEIS = ("leitor", "usuario", "revisor", "gerente", "admin")

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
- [ ] Todo método de serviço que depende de posse recebe o escopo de dono explicitamente.
- [ ] Rota nova está protegida — ou é pública **de propósito**, marcada com
      `@blp.doc(security=PUBLIC)` **e** acrescentada a `ROTAS_PUBLICAS`, em
      `tests/test_contracts.py`. O teste compara essa lista com o que o spec declara
      aberto, então abrir uma rota sem fazer as duas coisas reprova no CI.
- [ ] Rota nova declara a **permissão** que exige, e não só o token. (Esquecer reprova em
      `tests/test_contracts.py`, mas é melhor descobrir antes.) Cada método de escrita
      declara a sua: o piso de leitura da classe satisfaz o contrato, então só o teste de
      403 pega essa falta.
- [ ] Campo novo aparece na API só se estiver no schema.
- [ ] Teste cobrindo acesso de outro usuário (deve ser **404**) e, se a rota exige uma
      permissão que algum papel não tem, acesso sem ela em cada operação (deve ser **403** —
      `login(role=Role.LEITOR)` na criação; nas escritas sobre registro existente, rebaixe o
      dono com `set_role`).
- [ ] Nenhum segredo em arquivo versionado.
