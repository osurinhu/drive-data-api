"""papel silver: a conta de sistema da camada silver

Troca a restrição `ck_users_role` para aceitar o valor novo. O autogenerate não
detecta mudança de CHECK, por isso esta migração é escrita à mão.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Escritos à mão, e não importados de `authz` (o porquê está na 0001).
PAPEIS_ANTES = ("leitor", "usuario", "gerente", "admin")
PAPEIS_DEPOIS = (*PAPEIS_ANTES, "silver")


def _trocar_check(papeis: tuple[str, ...]) -> None:
    valores = ", ".join(f"'{papel}'" for papel in papeis)
    # `op.f()`: o nome já é final (sem ele, a convenção daria ck_users_ck_users_role).
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role"), "users", f"role IN ({valores})")


def upgrade() -> None:
    _trocar_check(PAPEIS_DEPOIS)


def downgrade() -> None:
    # Falha se ainda houver usuário com o papel silver, de propósito: converter a
    # conta de sistema em outro papel daria a ela permissões que ela nunca teve.
    # Antes do downgrade, troque o papel dessas contas (flask set-role) ou apague-as.
    _trocar_check(PAPEIS_ANTES)
