"""Serviço genérico: CRUD, exclusão lógica, ownership e ganchos de extensão.

## Como o ownership funciona

Entidades marcadas com ``owned=True`` pertencem a um usuário e só podem ser
vistas/alteradas por ele. Todo método público exige um **escopo de dono**
explícito — um id de usuário, ou a sentinela ``ANY_OWNER``, que
``ext.auth.current_owner_scope(permissão)`` concede a quem tem a permissão de
escopo correspondente.

O escopo é parâmetro de método (e não de construtor) e **não tem valor padrão**;
o porquê das duas escolhas está em `documentacao/02-decisoes.md`.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy.exc import IntegrityError

from domain.base import BaseModel
from repository.sql.base_repository import BaseRepository
from service.errors import BusinessRuleError, ConflictError, NotFoundError
from service.pagination import Page


class AnyOwner:
    """Sentinela que desativa o filtro de posse (uso administrativo)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "ANY_OWNER"


ANY_OWNER = AnyOwner()

#: Id do dono, ou ANY_OWNER para ignorar o filtro de posse.
OwnerScope = int | AnyOwner


class BaseService[ModelT: BaseModel]:
    #: Mensagens sobrescritas pelos serviços concretos.
    conflict_message = "Já existe um registro com esses dados."
    not_found_message = "Recurso não encontrado."

    #: Campo do modelo que guarda o dono (só usado quando `owned=True`).
    owner_field = "owner_id"

    def __init__(self, repository: BaseRepository[ModelT], owned: bool = False) -> None:
        self.repository = repository
        self.owned = owned

    # --- Ganchos de extensão --------------------------------------------------
    # Sobrescreva nos serviços concretos para acrescentar regras sem
    # reimplementar o fluxo das operações.

    def before_create(self, data: dict[str, Any]) -> None:
        """Chamado com os dados já validados, antes de instanciar o modelo.

        Em entidade com dono, ``data[owner_field]`` já está preenchido.
        """

    def before_update(self, model: ModelT, changes: dict[str, Any]) -> None:
        """Chamado com o modelo carregado e os campos que serão alterados."""

    def before_delete(self, model: ModelT) -> None:
        """Chamado antes da exclusão lógica. Levante para bloquear."""

    @staticmethod
    def _clean_text(value: str | None, campo: str) -> str:
        """Apara as pontas e rejeita o que sobrar vazio — para os ganchos usarem.

        Convive com o `validate.Length(min=1)` do schema, que pega a string
        vazia: aqui é o caso que passa por ele, o de só espaços. São 422 os dois,
        mas o do schema vem como erro de campo e este como mensagem solta.
        """
        limpo = (value or "").strip()
        if not limpo:
            raise BusinessRuleError(f"O {campo} não pode ser vazio.")
        return limpo

    # --- Ownership ------------------------------------------------------------

    def _owner_filter(self, owner_scope: OwnerScope) -> dict[str, Any]:
        if not self.owned or owner_scope is ANY_OWNER:
            return {}
        return {self.owner_field: owner_scope}

    def _assert_ownership(self, model: ModelT, owner_scope: OwnerScope) -> None:
        if not self.owned or owner_scope is ANY_OWNER:
            return
        if getattr(model, self.owner_field, None) != owner_scope:
            # NotFound (404), não PermissionDenied (403): um 403 confirmaria ao
            # solicitante que o recurso de outra pessoa existe.
            raise NotFoundError(self.not_found_message)

    def _require_concrete_owner(self, owner_scope: OwnerScope) -> int:
        if not isinstance(owner_scope, int):
            # TypeError, e não uma exceção de domínio: nenhuma requisição chega
            # aqui (a rota de criação passa `current_user.id`), então isto é
            # defeito de quem chamou. Vira 500 com traceback e `request_id` no
            # log, em vez de um 422 sobre o qual o cliente não pode agir.
            raise TypeError(f"Criar {self.repository.model.__name__} exige um dono concreto.")
        return owner_scope

    # --- Leitura --------------------------------------------------------------

    def get_by_id(self, model_id: int, owner_scope: OwnerScope) -> ModelT:
        model = self.repository.get_by_id(model_id)
        if model is None:
            raise NotFoundError(self.not_found_message)
        self._assert_ownership(model, owner_scope)
        return model

    def get_page(
        self,
        owner_scope: OwnerScope,
        *,
        page: int = 1,
        per_page: int = 20,
        exact: dict[str, Any] | None = None,
        like: dict[str, Any] | None = None,
        order_by: Any = None,
    ) -> Page[ModelT]:
        """Retorna o envelope de paginação com os modelos em ``items``.

        A serialização de cada item é feita pelo schema marshmallow da rota.
        """
        filters = dict(exact or {})
        filters.update(self._owner_filter(owner_scope))

        pagination = self.repository.get_page(
            exact=filters, like=like, page=page, per_page=per_page, order_by=order_by
        )
        return Page(
            items=list(pagination.items),
            current_page=pagination.page,
            total_of_items=pagination.total or 0,
            total_of_pages=pagination.pages,
            has_prev=pagination.has_prev,
            has_next=pagination.has_next,
        )

    # --- Escrita --------------------------------------------------------------

    def create(self, owner_scope: OwnerScope, *, commit: bool = True, **data: Any) -> ModelT:
        if self.owned:
            data[self.owner_field] = self._require_concrete_owner(owner_scope)

        self.before_create(data)
        model = self._guard_integrity(lambda: self.repository.add(self.repository.model(**data)))

        if commit:
            self.repository.commit()
        return model

    def create_many(
        self,
        owner_scope: OwnerScope,
        items: Iterable[dict[str, Any]],
        *,
        commit: bool = True,
    ) -> list[ModelT]:
        """Cria vários registros em uma única transação: se um falhar, nenhum
        é persistido."""
        created: list[ModelT] = []
        try:
            for data in items:
                created.append(self.create(owner_scope, commit=False, **data))
        except Exception:
            self.repository.rollback()
            raise

        if commit:
            self.repository.commit()
        return created

    def update(
        self, model_id: int, owner_scope: OwnerScope, *, commit: bool = True, **changes: Any
    ) -> ModelT:
        """Aplica exatamente os campos recebidos — nada é descartado por ser
        falsy. Como os schemas de PATCH são parciais, ``changes`` já contém
        somente o que o cliente enviou de fato.
        """
        model = self.get_by_id(model_id, owner_scope)
        self.before_update(model, changes)

        if changes:
            self._guard_integrity(lambda: self.repository.update(model, **changes))

        if commit:
            self.repository.commit()
        return model

    def delete(self, model_id: int, owner_scope: OwnerScope, *, commit: bool = True) -> ModelT:
        model = self.get_by_id(model_id, owner_scope)
        self.before_delete(model)
        self.repository.soft_delete(model)

        if commit:
            self.repository.commit()
        return model

    # --- Transação ------------------------------------------------------------

    def _guard_integrity(self, operation):
        """Traduz violação de restrição do banco em ConflictError (HTTP 409).

        O rollback é obrigatório: após um IntegrityError a sessão fica
        inutilizável até ser revertida.
        """
        try:
            return operation()
        except IntegrityError as error:
            self.repository.rollback()
            raise ConflictError(self.conflict_message) from error

    def commit(self) -> None:
        self.repository.commit()

    def rollback(self) -> None:
        self.repository.rollback()
