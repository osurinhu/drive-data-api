"""remove a entidade de exemplo: a tabela items e os papéis leitor e gerente

A troca do CHECK é escrita à mão porque o autogenerate não a detecta.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Escritos à mão, e não importados de `authz` (o porquê está na 0001).
PAPEIS_ANTES = ("leitor", "usuario", "gerente", "admin", "silver")
PAPEIS_DEPOIS = ("usuario", "admin", "silver")


def _trocar_check(papeis: tuple[str, ...]) -> None:
    valores = ", ".join(f"'{papel}'" for papel in papeis)
    # `op.f()`: o nome já é final (sem ele, a convenção daria ck_users_ck_users_role).
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role"), "users", f"role IN ({valores})")


def upgrade() -> None:
    # As contas dos papéis que saem viram `usuario`, sem o CHECK reprovar. O `leitor` passa a
    # escrever nos próprios veículos e a enviar telemetria, e o `gerente` deixa de ler os de
    # todos: confira antes de migrar quem está nesses papéis (README, "Antes de ir para produção").
    op.execute("UPDATE users SET role = 'usuario' WHERE role IN ('leitor', 'gerente')")
    _trocar_check(PAPEIS_DEPOIS)

    op.drop_index(op.f("uq_items_owner_name_active"), table_name="items")
    op.drop_index(op.f("ix_items_owner_id"), table_name="items")
    op.drop_table("items")


def downgrade() -> None:
    # Volta a estrutura, não os dados: os itens apagados não voltam, e quem era `leitor`
    # ou `gerente` continua `usuario`.
    _trocar_check(PAPEIS_ANTES)

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
    op.create_index(
        "uq_items_owner_name_active",
        "items",
        ["owner_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted = false"),
    )
