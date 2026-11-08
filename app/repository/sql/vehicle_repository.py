from collections.abc import Collection

from sqlalchemy import and_, or_, select

from domain.models import Vehicle, VehicleDriver
from domain.ports import AuthorizedVehicle, SenderRelation
from ext.database import db
from repository.sql.base_repository import BaseRepository


class VehicleRepository(BaseRepository[Vehicle]):
    def __init__(self) -> None:
        super().__init__(Vehicle)

    def authorized_vins(self, user_id: int, vins: Collection[str]) -> dict[str, AuthorizedVehicle]:
        """Implementa ``domain.ports.VehicleAuthorization``: o usuário envia pelo veículo
        ativo de que é gestor (``owner``) ou motorista ativo (``driver``).

        Uma consulta só, qualquer que seja o tamanho do lote: o VIN pelo
        ``uq_vehicles_vin_active`` e o vínculo pelo
        ``uq_vehicle_drivers_vehicle_driver_active``; lista vazia nem consulta. Como o VIN
        é único entre os ativos, e o vínculo ativo é único por par, cada VIN devolvido
        aponta para um veículo e um vínculo só.
        """
        if not vins:
            return {}
        # `select` do SQLAlchemy, e não `db.select`: é o que dá o tipo de cada coluna.
        stmt = (
            select(Vehicle.vin, Vehicle.id, (Vehicle.owner_id == user_id).label("is_owner"))
            .outerjoin(
                VehicleDriver,
                and_(
                    VehicleDriver.vehicle_id == Vehicle.id,
                    VehicleDriver.driver_id == user_id,
                    VehicleDriver.deleted.is_(False),
                ),
            )
            .where(
                Vehicle.vin.in_(sorted(set(vins))),
                Vehicle.deleted.is_(False),
                or_(Vehicle.owner_id == user_id, VehicleDriver.id.is_not(None)),
            )
        )
        return {
            vin: AuthorizedVehicle(
                vehicle_id=vehicle_id,
                relation=SenderRelation.OWNER if is_owner else SenderRelation.DRIVER,
            )
            for vin, vehicle_id, is_owner in db.session.execute(stmt)
        }
