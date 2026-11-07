"""schema inicial: users e items

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Os papéis são escritos à mão, e não importados de `authz`: uma migração
#: descreve o schema no momento em que foi escrita. Importando o enum,
#: acrescentar um papel amanhã mudaria esta migração retroativamente.
PAPEIS = ("leitor", "usuario", "gerente", "admin")


def upgrade() -> None:
    valores = ", ".join(f"'{papel}'" for papel in PAPEIS)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("password", sa.String(length=255), nullable=False),
        # VARCHAR + CHECK, não o ENUM nativo do Postgres: acrescentar um papel
        # vira uma troca de restrição em vez de `ALTER TYPE ... ADD VALUE`.
        sa.Column("role", sa.String(length=20), server_default="usuario", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("name", name=op.f("uq_users_name")),
        # `op.f()` marca o nome como final. Sem ele, a convenção do projeto
        # (`ck_%(table_name)s_%(constraint_name)s`) seria aplicada por cima e
        # produziria `ck_users_ck_users_role`.
        sa.CheckConstraint(f"role IN ({valores})", name=op.f("ck_users_role")),
    )

    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
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
            name=op.f("fk_items_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
    )
    op.create_index(op.f("ix_items_owner_id"), "items", ["owner_id"])

    # Único entre itens ATIVOS do mesmo dono: um item excluído logicamente não
    # deve bloquear a reutilização do nome.
    op.create_index(
        "uq_items_owner_name_active",
        "items",
        ["owner_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_items_owner_name_active", table_name="items")
    op.drop_index(op.f("ix_items_owner_id"), table_name="items")
    op.drop_table("items")
    op.drop_table("users")
