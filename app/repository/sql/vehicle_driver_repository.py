from sqlalchemy import update
from sqlalchemy.orm import joinedload

from domain.models import Vehicle, VehicleDriver
from ext.database import db
from repository.sql.base_repository import BaseRepository


class VehicleDriverRepository(BaseRepository[VehicleDriver]):
    #: Os schemas de saída mostram o login do motorista (`driver.name`) e, na lista dos
    #: veículos que ele dirige, o veículo e o login do gestor (`vehicle.owner.name`).
    #: Sem o eager load, cada vínculo da página custaria até três consultas. As três
    #: relações são muitos-para-um, então o `joinedload` as traz na consulta da página,
    #: sem multiplicar linhas; o `selectinload` custaria uma consulta a mais por
    #: relação, mesmo com o objeto já na sessão. Os números estão em
    #: `tests/test_read_cost.py`, que reprova se eles crescerem.
    loader_options = (
        joinedload(VehicleDriver.driver),
        joinedload(VehicleDriver.vehicle).joinedload(Vehicle.owner),
    )

    def __init__(self) -> None:
        super().__init__(VehicleDriver)

    def get_active(self, vehicle_id: int, driver_id: int) -> VehicleDriver | None:
        """O vínculo ativo do par, servido por ``uq_vehicle_drivers_vehicle_driver_active``."""
        return self.get_first_by_filter(vehicle_id=vehicle_id, driver_id=driver_id)

    def soft_delete_by_vehicle(self, vehicle_id: int) -> None:
        """Desliga todos os vínculos ativos do veículo, num UPDATE só e sem commit: quem
        exclui o veículo faz as duas escritas na mesma transação."""
        db.session.execute(
            update(VehicleDriver)
            .where(VehicleDriver.vehicle_id == vehicle_id, VehicleDriver.deleted.is_(False))
            .values(deleted=True)
        )
