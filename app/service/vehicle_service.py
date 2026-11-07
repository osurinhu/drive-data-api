"""Serviço de veículos.

Quem cadastra é o dono do veículo, o gestor. O VIN é gravado na forma canônica
(`vin.normalize_vin`), que é a forma em que a posse é consultada depois.
"""

from domain.models import Vehicle
from repository.sql.vehicle_repository import VehicleRepository
from service.base_service import BaseService
from service.errors import BusinessRuleError
from vin import INVALID_VIN_MESSAGE, is_valid_vin, normalize_vin


class VehicleService(BaseService[Vehicle]):
    conflict_message = "Este VIN já está cadastrado."
    not_found_message = "Veículo não encontrado."

    def __init__(self, repository: VehicleRepository) -> None:
        super().__init__(repository, owned=True)

    def before_create(self, data: dict) -> None:
        # O schema já recusou o formato; aqui ficam a forma canônica e a mesma
        # recusa para quem chama sem passar por schema (como a CLI).
        vin = data.get("vin") or ""
        if not is_valid_vin(vin):
            raise BusinessRuleError(INVALID_VIN_MESSAGE)
        data["vin"] = normalize_vin(vin)

    def before_update(self, model: Vehicle, changes: dict) -> None:
        # O VIN identifica o veículo e decide quem envia dados por ele: trocá-lo
        # faria de outro carro o dono destes vínculos. O schema do PATCH nem o aceita.
        if "vin" in changes:
            raise BusinessRuleError(
                "O VIN de um veículo não muda: exclua o veículo e cadastre outro."
            )
