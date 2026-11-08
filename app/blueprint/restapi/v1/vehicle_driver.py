"""Rotas dos motoristas: o sub-recurso `/vehicle/<id>/drivers` e a visão do motorista,
`/vehicle/driving`.

As permissões são as do veículo: quem lê o veículo lê os motoristas dele, e quem o
altera os altera. O escopo de dono vai para o serviço, que o aplica no veículo.
"""

from dependency_injector.wiring import Provide, inject
from flask.views import MethodView
from flask_jwt_extended import current_user
from flask_smorest import Blueprint

from blueprint.http_errors import error_responses
from domain.authz import Permission
from ext.auth import current_owner_scope, permission_required
from ext.container import Container
from schemas.pagination_schema import PaginationQuerySchema
from schemas.vehicle_driver_schema import (
    DriverCreateSchema,
    DriverPageSchema,
    DriverSchema,
    DrivingPageSchema,
    DrivingSchema,
)
from service.vehicle_driver_service import VehicleDriverService

blp = Blueprint(
    "vehicle_driver",
    __name__,
    url_prefix="/vehicle",
    description="Motoristas dos veículos. O gestor os adiciona pelo login; o motorista "
    "envia dados do veículo enquanto o vínculo estiver ativo.",
)


class DriverView(MethodView):
    """Base dos recursos de motorista: o piso de permissão e o serviço injetado."""

    decorators = [permission_required(Permission.VEICULO_LER)]

    @inject
    def __init__(
        self,
        driver_service: VehicleDriverService = Provide[Container.vehicle_driver_service],
    ) -> None:
        self.driver_service = driver_service


@blp.route("/<int:vehicle_id>/drivers")
class VehicleDrivers(DriverView):
    @blp.arguments(PaginationQuerySchema, location="query")
    @blp.response(200, DriverPageSchema)
    @error_responses(blp, 403, 404)
    def get(self, query: dict, vehicle_id: int):
        """Lista os motoristas ativos de um veículo do usuário autenticado — ou de
        qualquer veículo, para quem tiver a permissão de escopo."""
        return self.driver_service.list_drivers(
            vehicle_id,
            current_owner_scope(Permission.VEICULO_LER_DE_TODOS),
            page=query["page"],
            per_page=query["per_page"],
        )

    @permission_required(Permission.VEICULO_ESCREVER)
    @blp.arguments(DriverCreateSchema)
    @blp.response(201, DriverSchema)
    @error_responses(blp, 403, 404, 409)
    def post(self, payload: dict, vehicle_id: int):
        """Adiciona um motorista ao veículo, pelo login dele.

        A partir daí ele pode enviar dados do veículo. Login inexistente responde 404;
        a pessoa que já é motorista do veículo, 409; o próprio gestor, 422 (ele já pode
        enviar).
        """
        return self.driver_service.add_driver(
            vehicle_id,
            current_owner_scope(Permission.VEICULO_ESCREVER_DE_TODOS),
            user_name=payload["user_name"],
        )


@blp.route("/<int:vehicle_id>/drivers/<int:driver_id>")
class VehicleDriverResource(DriverView):
    @permission_required(Permission.VEICULO_ESCREVER)
    @blp.response(200, DriverSchema)
    @error_responses(blp, 403, 404)
    def delete(self, vehicle_id: int, driver_id: int):
        """Remove um motorista do veículo.

        O envio seguinte dele pelo veículo volta como `vehicle_not_authorized`. O que
        ele já enviou continua no bronze.
        """
        return self.driver_service.remove_driver(
            vehicle_id, driver_id, current_owner_scope(Permission.VEICULO_ESCREVER_DE_TODOS)
        )


@blp.route("/driving")
class DrivingCollection(DriverView):
    @blp.arguments(PaginationQuerySchema, location="query")
    @blp.response(200, DrivingPageSchema)
    @error_responses(blp, 403)
    def get(self, query: dict):
        """Lista os veículos de que o usuário autenticado é motorista."""
        # current_user.id, e não um escopo: a lista é a do próprio motorista.
        return self.driver_service.list_driving(
            current_user.id, page=query["page"], per_page=query["per_page"]
        )


@blp.route("/driving/<int:vehicle_id>")
class DrivingResource(DriverView):
    @permission_required(Permission.VEICULO_ESCREVER)
    @blp.response(200, DrivingSchema)
    @error_responses(blp, 403, 404)
    def delete(self, vehicle_id: int):
        """Deixa de ser motorista de um veículo.

        Vale na hora: o envio seguinte pelo veículo volta como `vehicle_not_authorized`.
        """
        return self.driver_service.leave(vehicle_id, current_user.id)
