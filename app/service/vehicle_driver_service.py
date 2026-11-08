"""Serviço dos motoristas: um sub-recurso que herda a posse do veículo.

O gestor (o dono do veículo) adiciona e remove motoristas pelo login. O motorista vê
os veículos que dirige e pode se desvincular deles. Quem pode mexer nos motoristas de
um veículo é quem pode mexer no veículo: toda operação do gestor carrega o veículo
pelo ``VehicleService.get_by_id`` com o escopo recebido, que dá 404 para o veículo de
outro dono, e só então toca nos vínculos. As do motorista partem do id dele, que a
rota tira do token.

O vínculo em si não tem dono (``owned=False``): o filtro de posse é o do veículo, já
aplicado, ou o do motorista, explícito em cada consulta.
"""

from domain.authz import ANY_OWNER, OwnerScope
from domain.models import VehicleDriver
from repository.sql.user_repository import UserRepository
from repository.sql.vehicle_driver_repository import VehicleDriverRepository
from service.base_service import BaseService
from service.errors import BusinessRuleError, NotFoundError
from service.pagination import Page
from service.vehicle_service import VehicleService

UNKNOWN_USER_MESSAGE = "Usuário não encontrado."
NOT_DRIVING_MESSAGE = "Veículo não encontrado."
OWNER_AS_DRIVER_MESSAGE = (
    "O gestor já envia dados do próprio veículo: não precisa ser motorista dele."
)


class VehicleDriverService(BaseService[VehicleDriver]):
    #: Redeclarado com o tipo concreto: o `get_active` só existe no repositório dos vínculos.
    repository: VehicleDriverRepository

    conflict_message = "Esta pessoa já é motorista deste veículo."
    not_found_message = "Motorista não encontrado."

    def __init__(
        self,
        repository: VehicleDriverRepository,
        *,
        vehicles: VehicleService,
        users: UserRepository,
    ) -> None:
        super().__init__(repository, owned=False)
        self.vehicles = vehicles
        self.users = users

    # --- O gestor -------------------------------------------------------------

    def list_drivers(
        self, vehicle_id: int, owner_scope: OwnerScope, *, page: int, per_page: int
    ) -> Page[VehicleDriver]:
        vehicle = self.vehicles.get_by_id(vehicle_id, owner_scope)
        # ANY_OWNER: a posse já foi conferida no veículo, e o vínculo não tem dono.
        return self.get_page(
            ANY_OWNER, page=page, per_page=per_page, exact={"vehicle_id": vehicle.id}
        )

    def add_driver(
        self, vehicle_id: int, owner_scope: OwnerScope, *, user_name: str
    ) -> VehicleDriver:
        """Faz do usuário de login ``user_name`` motorista do veículo. A pessoa não
        precisa aceitar; pode se desvincular depois (``leave``)."""
        vehicle = self.vehicles.get_by_id(vehicle_id, owner_scope)
        user = self.users.get_by_name(self._clean_text(user_name, "login do motorista"))
        if user is None:
            raise NotFoundError(UNKNOWN_USER_MESSAGE)
        if user.id == vehicle.owner_id:
            raise BusinessRuleError(OWNER_AS_DRIVER_MESSAGE)
        # O vínculo ativo repetido esbarra no índice único parcial: 409.
        return self.create(ANY_OWNER, vehicle_id=vehicle.id, driver_id=user.id)

    def remove_driver(
        self, vehicle_id: int, driver_id: int, owner_scope: OwnerScope
    ) -> VehicleDriver:
        vehicle = self.vehicles.get_by_id(vehicle_id, owner_scope)
        link = self.repository.get_active(vehicle.id, driver_id)
        if link is None:
            raise NotFoundError(self.not_found_message)
        return self._end(link)

    # --- O motorista ----------------------------------------------------------

    def list_driving(self, driver_id: int, *, page: int, per_page: int) -> Page[VehicleDriver]:
        """Os veículos que ``driver_id`` dirige. O filtro é o próprio motorista, e não um
        escopo: a lista não tem versão "de todos"."""
        return self.get_page(
            ANY_OWNER, page=page, per_page=per_page, exact={"driver_id": driver_id}
        )

    def leave(self, vehicle_id: int, driver_id: int) -> VehicleDriver:
        """O motorista se desvincula. Para ele, o veículo que não dirige não existe."""
        link = self.repository.get_active(vehicle_id, driver_id)
        if link is None:
            raise NotFoundError(NOT_DRIVING_MESSAGE)
        return self._end(link)

    # --- Interno --------------------------------------------------------------

    def _end(self, link: VehicleDriver) -> VehicleDriver:
        self.repository.soft_delete(link)
        self.repository.commit()
        return link
