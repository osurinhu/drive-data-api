from collections.abc import Collection

from sqlalchemy import select

from domain.models import Vehicle
from ext.database import db
from ports import AuthorizedVehicle, SenderRelation
from repository.sql.base_repository import BaseRepository


class VehicleRepository(BaseRepository[Vehicle]):
    def __init__(self) -> None:
        super().__init__(Vehicle)

    def authorized_vins(self, user_id: int, vins: Collection[str]) -> dict[str, AuthorizedVehicle]:
        """Implementa ``ports.VehicleAuthorization``. Hoje o único vínculo é o de
        dono: o cadastro de motoristas ainda não existe.

        Uma consulta só, qualquer que seja o tamanho do lote, servida pelo índice
        ``uq_vehicles_vin_active``; lista vazia nem consulta. Como o VIN é único
        entre os ativos, cada VIN devolvido aponta para um veículo só.
        """
        if not vins:
            return {}
        # `select` do SQLAlchemy, e não `db.select`: é o que dá o tipo de cada coluna.
        stmt = select(Vehicle.vin, Vehicle.id).where(
            Vehicle.vin.in_(sorted(set(vins))),
            Vehicle.owner_id == user_id,
            Vehicle.deleted.is_(False),
        )
        return {
            vin: AuthorizedVehicle(vehicle_id=vehicle_id, relation=SenderRelation.OWNER)
            for vin, vehicle_id in db.session.execute(stmt)
        }
