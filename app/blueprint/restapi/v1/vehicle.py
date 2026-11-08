"""Rotas de veículos.

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
from schemas.vehicle_schema import (
    VehicleCreateSchema,
    VehiclePageSchema,
    VehicleQuerySchema,
    VehicleSchema,
    VehicleUpdateSchema,
)
from service.vehicle_service import VehicleService

blp = Blueprint(
    "vehicle",
    __name__,
    url_prefix="/vehicle",
    description="Cadastro de veículos. Quem cadastra é o gestor do veículo.",
)


class VehicleView(MethodView):
    """Base dos recursos de Vehicle: declara o piso de permissão e injeta o serviço."""

    decorators = [permission_required(Permission.VEICULO_LER)]

    @inject
    def __init__(
        self, vehicle_service: VehicleService = Provide[Container.vehicle_service]
    ) -> None:
        self.vehicle_service = vehicle_service


@blp.route("/")
class VehicleCollection(VehicleView):
    @blp.arguments(VehicleQuerySchema, location="query")
    @blp.response(200, VehiclePageSchema)
    @error_responses(blp, 403)
    def get(self, query: dict):
        """Lista os veículos do usuário autenticado — ou os de todos, para quem
        tiver a permissão de escopo."""
        return self.vehicle_service.get_page(
            current_owner_scope(Permission.VEICULO_LER_DE_TODOS),
            page=query["page"],
            per_page=query["per_page"],
            like={"vin": query["vin"]},
        )

    @permission_required(Permission.VEICULO_ESCREVER)
    @blp.arguments(VehicleCreateSchema)
    @blp.response(201, VehicleSchema)
    @error_responses(blp, 403, 409)
    def post(self, payload: dict):
        """Cadastra um veículo para o usuário autenticado, que passa a ser o gestor dele.

        O VIN é único entre os veículos ativos: se ele já tiver um cadastro ativo,
        de qualquer usuário, a resposta é 409.
        """
        # current_user.id, e não current_owner_scope(...): criar exige um dono concreto.
        return self.vehicle_service.create(current_user.id, **payload)


@blp.route("/<int:vehicle_id>")
class VehicleResource(VehicleView):
    @blp.response(200, VehicleSchema)
    @error_responses(blp, 403, 404)
    def get(self, vehicle_id: int):
        """Busca um veículo por id."""
        return self.vehicle_service.get_by_id(
            vehicle_id, current_owner_scope(Permission.VEICULO_LER_DE_TODOS)
        )

    @permission_required(Permission.VEICULO_ESCREVER)
    @blp.arguments(VehicleUpdateSchema)
    @blp.response(200, VehicleSchema)
    @error_responses(blp, 403, 404)
    def patch(self, payload: dict, vehicle_id: int):
        """Altera o apelido de um veículo.

        O VIN não muda: para corrigi-lo, exclua o veículo e cadastre de novo.
        """
        return self.vehicle_service.update(
            vehicle_id, current_owner_scope(Permission.VEICULO_ESCREVER_DE_TODOS), **payload
        )

    @permission_required(Permission.VEICULO_ESCREVER)
    @blp.response(200, VehicleSchema)
    @error_responses(blp, 403, 404)
    def delete(self, vehicle_id: int):
        """Exclui logicamente um veículo (marca `deleted`, não remove a linha).

        O VIN fica livre para um novo cadastro.
        """
        return self.vehicle_service.delete(
            vehicle_id, current_owner_scope(Permission.VEICULO_ESCREVER_DE_TODOS)
        )
