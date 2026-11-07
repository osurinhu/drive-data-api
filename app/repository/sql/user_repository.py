from domain.models import User
from repository.sql.base_repository import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self) -> None:
        super().__init__(User)

    def get_by_name(self, name: str) -> User | None:
        return self.get_first_by_filter(name=name)
