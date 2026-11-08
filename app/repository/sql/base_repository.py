"""Repositório genérico: CRUD completo parametrizado por modelo.

Cada repositório concreto é uma subclasse de uma linha que apenas amarra o
modelo. Consultas específicas de uma entidade entram como métodos novos na
subclasse — nunca dentro de um serviço.
"""

from typing import Any

from flask_sqlalchemy.pagination import Pagination
from sqlalchemy import Select

from domain.base import BaseModel
from ext.database import db


class BaseRepository[ModelT: BaseModel]:
    """Parametrizado pelo modelo que manipula, para que
    ``UserRepository.get_by_id()`` seja tipado como ``User | None`` e não como
    ``BaseModel | None`` — é o que permite ao type checker pegar camada trocada.
    """

    #: Estratégias de carregamento aplicadas a toda consulta deste repositório.
    #: É aqui que se evita N+1: no dia em que um schema aninhar um
    #: relacionamento, declare `loader_options = (joinedload(Item.owner),)` (ou
    #: `selectinload`, numa coleção) e o eager load vale para todas as consultas
    #: de uma vez.
    loader_options: tuple = ()

    def __init__(self, model: type[ModelT]) -> None:
        self.model = model

    # --- Construção de consultas ---------------------------------------------

    def _select(self, include_deleted: bool = False) -> Select:
        stmt = db.select(self.model)
        if self.loader_options:
            stmt = stmt.options(*self.loader_options)
        if not include_deleted:
            stmt = stmt.where(self.model.deleted.is_(False))
        return stmt

    def _apply_like(self, stmt: Select, like: dict[str, Any] | None) -> Select:
        """Adiciona filtros de correspondência parcial, ignorando os vazios.

        (Nos projetos de origem a condição aqui era sempre verdadeira, então
        todo filtro vazio virava uma correspondência inútil na consulta.)
        """
        for field, value in (like or {}).items():
            if value is None or value == "":
                continue
            # ilike: correspondência sem diferenciar maiúsculas (Postgres).
            stmt = stmt.where(getattr(self.model, field).ilike(f"%{value}%"))
        return stmt

    # --- Leitura --------------------------------------------------------------

    def get_by_id(self, model_id: int, include_deleted: bool = False) -> ModelT | None:
        stmt = self._select(include_deleted).where(self.model.id == model_id)
        return db.session.execute(stmt).scalar_one_or_none()

    def get_by_filter(
        self,
        limit: int | None = None,
        include_deleted: bool = False,
        **exact: Any,
    ) -> list[ModelT]:
        stmt = self._select(include_deleted).filter_by(**exact)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(db.session.execute(stmt).scalars().all())

    def get_first_by_filter(self, include_deleted: bool = False, **exact: Any) -> ModelT | None:
        results = self.get_by_filter(limit=1, include_deleted=include_deleted, **exact)
        return results[0] if results else None

    def get_page(
        self,
        exact: dict[str, Any] | None = None,
        like: dict[str, Any] | None = None,
        page: int = 1,
        per_page: int = 20,
        include_deleted: bool = False,
        order_by: Any = None,
    ) -> Pagination:
        """Página de resultados. ``order_by`` aceita qualquer critério do
        SQLAlchemy; sem ele, ordena por id (ordem estável e indexada).

        Atenção com tabelas grandes: a paginação por OFFSET fica mais lenta
        conforme a página avança, e cada chamada emite um COUNT. Para listagens
        muito grandes, prefira paginação por cursor num método da subclasse.
        """
        stmt = self._select(include_deleted).filter_by(**(exact or {}))
        stmt = self._apply_like(stmt, like)
        stmt = stmt.order_by(self.model.id if order_by is None else order_by)
        return db.paginate(stmt, page=page, per_page=per_page, error_out=False)

    # --- Escrita --------------------------------------------------------------

    def add(self, model: ModelT) -> ModelT:
        """Insere o objeto e faz flush, para que o id esteja disponível antes do
        commit — é isso que permite compor várias escritas numa só transação.

        **Sem `refresh()` depois do flush**, de propósito: o `commit()` do
        serviço já expira os atributos, então o refresh buscava o registro uma
        segunda vez. Os números medidos estão em `tests/test_write_cost.py`, que
        reprova se alguém o reintroduzir.
        """
        db.session.add(model)
        db.session.flush()
        return model

    def update(self, model: ModelT, **changes: Any) -> ModelT:
        for field, value in changes.items():
            setattr(model, field, value)
        db.session.flush()
        return model

    def soft_delete(self, model: ModelT) -> ModelT:
        return self.update(model, deleted=True)

    # --- Transação ------------------------------------------------------------

    def commit(self) -> None:
        db.session.commit()

    def rollback(self) -> None:
        db.session.rollback()
