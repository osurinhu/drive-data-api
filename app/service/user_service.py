"""Serviço de usuários.

Entidade global (``owned=False``): não pertence a ninguém, então o escopo de
dono é irrelevante e as chamadas internas passam ``ANY_OWNER``.
"""

from werkzeug.security import generate_password_hash

from authz import Role
from domain.models import User
from repository.user_repository import UserRepository
from service.base_service import ANY_OWNER, BaseService
from service.errors import BusinessRuleError, NotFoundError

MIN_PASSWORD_LENGTH = 8


class UserService(BaseService[User]):
    #: Redeclarado com o tipo concreto: `BaseService` guarda um `BaseRepository`, e
    #: `set_role` precisa do `get_by_name`, que só o de usuário tem.
    repository: UserRepository

    conflict_message = "Já existe um usuário com esse nome."
    not_found_message = "Usuário não encontrado."

    def __init__(self, repository: UserRepository) -> None:
        super().__init__(repository, owned=False)

    def before_create(self, data: dict) -> None:
        data["name"] = self._clean_text(data.get("name"), "nome do usuário")

    def register(
        self, name: str, password: str, role: Role = Role.USUARIO, *, commit: bool = True
    ) -> User:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise BusinessRuleError(f"A senha deve ter no mínimo {MIN_PASSWORD_LENGTH} caracteres.")

        return self.create(
            ANY_OWNER,
            commit=commit,
            name=name,
            password=generate_password_hash(password),
            role=role,
        )

    def set_role(self, name: str, role: Role, *, commit: bool = True) -> User:
        """Troca o papel de um usuário, pelo nome de login.

        Vale a partir da requisição seguinte, com o mesmo token — o papel não
        está assinado em lugar nenhum.
        """
        user = self.repository.get_by_name(name)
        if user is None:
            raise NotFoundError(f"Usuário '{name}' não encontrado.")
        return self.update(user.id, ANY_OWNER, commit=commit, role=role)
