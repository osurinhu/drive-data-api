"""Serviço da entidade de exemplo.

Mostra o padrão a seguir em qualquer entidade nova: estender BaseService,
declarar as mensagens de erro, marcar se a entidade tem dono e implementar
apenas os ganchos que precisam de regra própria.
"""

from domain.models import Item
from repository.sql.item_repository import ItemRepository
from service.base_service import BaseService


class ItemService(BaseService[Item]):
    conflict_message = "Você já tem um item ativo com esse nome."
    not_found_message = "Item não encontrado."

    def __init__(self, repository: ItemRepository) -> None:
        super().__init__(repository, owned=True)

    def before_create(self, data: dict) -> None:
        data["name"] = self._clean_text(data.get("name"), "nome do item")

    def before_update(self, model: Item, changes: dict) -> None:
        if "name" in changes:
            changes["name"] = self._clean_text(changes["name"], "nome do item")
