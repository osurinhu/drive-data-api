"""adiciona vehicles: o cadastro de veículos, com o VIN único entre os ativos

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("nickname", sa.String(length=50), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
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
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_vehicles_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicles")),
    )
    op.create_index(op.f("ix_vehicles_owner_id"), "vehicles", ["owner_id"])

    # Único entre os veículos ATIVOS, de qualquer dono (e não por dono, como o nome
    # do item): dois cadastros ativos do mesmo VIN seriam dois gestores do mesmo
    # carro. A consulta de posse do envio também passa por este índice.
    op.create_index(
        "uq_vehicles_vin_active",
        "vehicles",
        ["vin"],
        unique=True,
        postgresql_where=sa.text("deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_vehicles_vin_active", table_name="vehicles")
    op.drop_index(op.f("ix_vehicles_owner_id"), table_name="vehicles")
    op.drop_table("vehicles")
