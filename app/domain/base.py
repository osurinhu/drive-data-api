"""Classe base dos modelos.

Os campos de controle declarados aqui são herdados por toda entidade, o que dá
a todas elas timestamps e exclusão lógica sem repetição.

Os modelos **não** carregam mixin de serialização: quem decide o que sai da API
são os schemas marshmallow (`app/schemas/`), e só eles.
"""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, false, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Sem convenção, cada banco inventa o nome que quiser e a migração não consegue
# referenciar uma restrição para alterá-la ou removê-la depois.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(column_0_label)s",
}


class BaseModel(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # A chave primária fica aqui, e não em cada modelo, porque `BaseRepository`
    # já dependia da existência de `id` sem que nada garantisse isso. Entidade
    # com chave diferente (composta, UUID) sobrescreve este campo.
    id: Mapped[int] = mapped_column(primary_key=True)

    # `timezone=True` -> TIMESTAMPTZ. Sem ele o instante fica ambíguo, e trocar
    # depois, com a tabela cheia, custa um `ALTER TYPE` caro. A regra `DTZ` do
    # ruff impede `datetime` naive entrar no código.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_update: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Exclusão lógica: o repositório filtra `deleted = false` em toda consulta.
    deleted: Mapped[bool] = mapped_column(default=False, server_default=false())
