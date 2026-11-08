"""Modelos da aplicação.

``Item`` é a entidade de exemplo: existe para demonstrar o caminho completo
(modelo → repositório → serviço → rota → schema → teste) e pode ser removida
ao iniciar um projeto real.
"""

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from domain.authz import Role
from domain.base import BaseModel

#: VARCHAR + CHECK, e não o tipo ENUM nativo do Postgres (o porquê está em
#: `documentacao/02-decisoes.md`). Os dois argumentos que silenciosamente mudam
#: tudo:
#:   create_constraint=True  o padrão do SQLAlchemy 2.0 é False, e sem ele não
#:                           haveria restrição nenhuma — só um VARCHAR livre.
#:   values_callable         sem ele o SQLAlchemy grava o *nome* do membro
#:                           ("ADMIN"), e não o valor ("admin").
ROLE_COLUMN_TYPE = SAEnum(
    Role,
    name="role",
    native_enum=False,
    length=20,
    create_constraint=True,
    values_callable=lambda enum: [member.value for member in enum],
)


class User(BaseModel):
    __tablename__ = "users"

    # `id`, `created_at`, `last_update` e `deleted` vêm de BaseModel.
    name: Mapped[str] = mapped_column(String(50), unique=True)
    password: Mapped[str] = mapped_column(String(255))

    # O papel mora **só aqui** — não há claim equivalente no token (ver `ext/auth.py`).
    role: Mapped[Role] = mapped_column(
        ROLE_COLUMN_TYPE, default=Role.USUARIO, server_default=Role.USUARIO.value
    )

    items: Mapped[list["Item"]] = relationship(back_populates="owner")
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="owner")
    #: Os vínculos de motorista: os veículos que o usuário dirige, de outros gestores.
    driving: Mapped[list["VehicleDriver"]] = relationship(back_populates="driver")

    def __repr__(self) -> str:
        return f"<User id={self.id} name={self.name!r} role={self.role.value!r}>"


class Item(BaseModel):
    __tablename__ = "items"
    __table_args__ = (
        # Índice único parcial: o nome só precisa ser único entre os itens
        # ATIVOS do mesmo dono. Sem o `WHERE`, um item excluído logicamente
        # bloquearia para sempre a reutilização do nome.
        Index(
            "uq_items_owner_name_active",
            "owner_id",
            "name",
            unique=True,
            postgresql_where=text("deleted = false"),
        ),
    )

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), default=None)

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    owner: Mapped["User"] = relationship(back_populates="items")

    def __repr__(self) -> str:
        return f"<Item id={self.id} name={self.name!r} owner_id={self.owner_id}>"


class Vehicle(BaseModel):
    """Veículo cadastrado. O dono (``owner``) é o gestor: quem o cadastrou."""

    __tablename__ = "vehicles"
    __table_args__ = (
        # Único entre os veículos ATIVOS, de qualquer dono: o VIN identifica o
        # carro, e é por ele que se descobre quem pode enviar dados dele. Dois
        # cadastros ativos do mesmo VIN seriam dois gestores para o mesmo carro.
        Index(
            "uq_vehicles_vin_active",
            "vin",
            unique=True,
            postgresql_where=text("deleted = false"),
        ),
    )

    # Sempre na forma canônica (`domain.vin.normalize_vin`), gravada pelo serviço.
    vin: Mapped[str] = mapped_column(String(17))
    nickname: Mapped[str | None] = mapped_column(String(50), default=None)

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    owner: Mapped["User"] = relationship(back_populates="vehicles")
    drivers: Mapped[list["VehicleDriver"]] = relationship(back_populates="vehicle")

    def __repr__(self) -> str:
        return f"<Vehicle id={self.id} vin={self.vin!r} owner_id={self.owner_id}>"


class VehicleDriver(BaseModel):
    """Vínculo de motorista: o gestor do veículo autorizou o usuário a enviar dados dele.

    Tirar o motorista é exclusão lógica do vínculo, e excluir o veículo desliga todos
    os vínculos dele (``VehicleService.before_delete``).
    """

    __tablename__ = "vehicle_drivers"
    __table_args__ = (
        # Um vínculo ATIVO por par: a mesma pessoa não é motorista duas vezes do
        # mesmo carro, e volta a poder ser depois de removida. É também o índice
        # da consulta de posse do envio (`VehicleRepository.authorized_vins`).
        Index(
            "uq_vehicle_drivers_vehicle_driver_active",
            "vehicle_id",
            "driver_id",
            unique=True,
            postgresql_where=text("deleted = false"),
        ),
    )

    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    vehicle: Mapped["Vehicle"] = relationship(back_populates="drivers")

    driver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    driver: Mapped["User"] = relationship(back_populates="driving")

    def __repr__(self) -> str:
        return (
            f"<VehicleDriver id={self.id} vehicle_id={self.vehicle_id} driver_id={self.driver_id}>"
        )
