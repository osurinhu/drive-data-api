from domain.models import Item
from repository.sql.base_repository import BaseRepository


class ItemRepository(BaseRepository[Item]):
    #: Se algum schema passar a aninhar um relacionamento (ex. `owner`),
    #: declare o eager load aqui para não cair em N+1 (muitos-para-um vai com
    #: `joinedload`; coleção, com `selectinload`):
    #: loader_options = (joinedload(Item.owner),)
    def __init__(self) -> None:
        super().__init__(Item)
