"""Rotas da entidade de exemplo.

Molde para qualquer recurso novo. Note o quanto a rota **não** faz: nenhum
try/except (quem traduz exceção é `ext.error_handler`), nenhuma montagem de
consulta (é do repositório), nenhuma serialização manual (é do schema) e nenhuma
decisão de permissão escrita à mão — ela é *declarada*.
"""

from dependency_injector.wiring import Provide, inject
from flask.views import MethodView
from flask_jwt_extended import current_user
from flask_smorest import Blueprint

from blueprint.http_errors import error_responses
from domain.authz import Permission
from ext.auth import current_owner_scope, permission_required
from ext.container import Container
from schemas.item_schema import (
    ItemCreateSchema,
    ItemPageSchema,
    ItemQuerySchema,
    ItemSchema,
    ItemUpdateSchema,
)
from service.item_service import ItemService

blp = Blueprint(
    "item",
    __name__,
    url_prefix="/item",
    description="Operações CRUD da entidade de exemplo",
)


class ItemView(MethodView):
    """Base dos recursos de Item: declara o piso de permissão e injeta o serviço.

    `decorators` vale para todos os métodos HTTP da classe; os que exigem mais
    acrescentam o seu próprio `@permission_required`. A verificação roda antes
    do `__init__`, então o container nem é consultado em requisição sem acesso.
    """

    decorators = [permission_required(Permission.ITEM_LER)]

    @inject
    def __init__(self, item_service: ItemService = Provide[Container.item_service]) -> None:
        self.item_service = item_service


@blp.route("/")
class ItemCollection(ItemView):
    @blp.arguments(ItemQuerySchema, location="query")
    @blp.response(200, ItemPageSchema)
    @error_responses(blp, 403)
    def get(self, query: dict):
        """Lista os itens do usuário autenticado — ou os de todos, para quem
        tiver a permissão de escopo."""
        return self.item_service.get_page(
            current_owner_scope(Permission.ITEM_LER_DE_TODOS),
            page=query["page"],
            per_page=query["per_page"],
            like={"name": query["name"]},
        )

    @permission_required(Permission.ITEM_ESCREVER)
    @blp.arguments(ItemCreateSchema)
    @blp.response(201, ItemSchema)
    @error_responses(blp, 403, 409)
    def post(self, payload: dict):
        """Cria um item para o usuário autenticado."""
        # current_user.id, e não current_owner_scope(...): criar exige um dono
        # concreto, então nem administrador cria "sem dono".
        return self.item_service.create(current_user.id, **payload)


@blp.route("/<int:item_id>")
class ItemResource(ItemView):
    @blp.response(200, ItemSchema)
    @error_responses(blp, 403, 404)
    def get(self, item_id: int):
        """Busca um item por id."""
        return self.item_service.get_by_id(
            item_id, current_owner_scope(Permission.ITEM_LER_DE_TODOS)
        )

    @permission_required(Permission.ITEM_ESCREVER)
    @blp.arguments(ItemUpdateSchema)
    @blp.response(200, ItemSchema)
    @error_responses(blp, 403, 404, 409)
    def patch(self, payload: dict, item_id: int):
        """Atualiza parcialmente um item."""
        return self.item_service.update(
            item_id, current_owner_scope(Permission.ITEM_ESCREVER_DE_TODOS), **payload
        )

    @permission_required(Permission.ITEM_ESCREVER)
    @blp.response(200, ItemSchema)
    @error_responses(blp, 403, 404)
    def delete(self, item_id: int):
        """Exclui logicamente um item (marca `deleted`, não remove a linha)."""
        return self.item_service.delete(
            item_id, current_owner_scope(Permission.ITEM_ESCREVER_DE_TODOS)
        )
