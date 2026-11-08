"""adiciona vehicle_drivers: os motoristas de cada veículo, um vínculo ativo por par

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicle_drivers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_update",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name=op.f("fk_vehicle_drivers_vehicle_id_vehicles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["driver_id"],
            ["users.id"],
            name=op.f("fk_vehicle_drivers_driver_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_drivers")),
    )
    # Os dois índices das FKs cobrem também os vínculos excluídos: são os que o
    # CASCADE usa ao apagar o veículo ou o usuário. O do motorista serve ainda a
    # listagem dos veículos que ele dirige.
    op.create_index(op.f("ix_vehicle_drivers_vehicle_id"), "vehicle_drivers", ["vehicle_id"])
    op.create_index(op.f("ix_vehicle_drivers_driver_id"), "vehicle_drivers", ["driver_id"])

    # Um vínculo ATIVO por par (veículo, motorista): a pessoa não é motorista duas
    # vezes do mesmo carro, e volta a poder ser depois de removida. A consulta de
    # posse do envio passa por este índice.
    op.create_index(
        "uq_vehicle_drivers_vehicle_driver_active",
        "vehicle_drivers",
        ["vehicle_id", "driver_id"],
        unique=True,
        postgresql_where=sa.text("deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_vehicle_drivers_vehicle_driver_active", table_name="vehicle_drivers")
    op.drop_index(op.f("ix_vehicle_drivers_driver_id"), table_name="vehicle_drivers")
    op.drop_index(op.f("ix_vehicle_drivers_vehicle_id"), table_name="vehicle_drivers")
    op.drop_table("vehicle_drivers")
