"""Serviço de usuários.

Entidade global (``owned=False``): não pertence a ninguém, então o escopo de
dono é irrelevante e as chamadas internas passam ``ANY_OWNER``.
"""

from werkzeug.security import generate_password_hash

from domain.authz import ANY_OWNER, Role
from domain.models import User
from repository.sql.refresh_token_repository import RefreshTokenRepository
from repository.sql.user_repository import UserRepository
from service.base_service import BaseService
from service.errors import BusinessRuleError, NotFoundError

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class UserService(BaseService[User]):
    #: Redeclarado com o tipo concreto: `BaseService` guarda um `BaseRepository`, e
    #: `set_role` precisa do `get_by_name`, que só o de usuário tem.
    repository: UserRepository

    conflict_message = "Já existe um usuário com esse nome."
    not_found_message = "Usuário não encontrado."

    def __init__(self, repository: UserRepository, *, tokens: RefreshTokenRepository) -> None:
        super().__init__(repository, owned=False)
        self.tokens = tokens

    def before_create(self, data: dict) -> None:
        data["name"] = self._clean_text(data.get("name"), "nome do usuário")

    def register(
        self, name: str, password: str, role: Role = Role.USUARIO, *, commit: bool = True
    ) -> User:
        self._check_password(password)
        return self.create(
            ANY_OWNER,
            commit=commit,
            name=name,
            password=generate_password_hash(password),
            role=role,
        )

    def set_password(self, name: str, password: str) -> User:
        """Troca a senha de um usuário e encerra todas as sessões dele, numa transação só:
        quem tinha a senha antiga não pode continuar renovando o token.
        """
        self._check_password(password)
        user = self._get_by_name(name)
        self.tokens.revoke_all_of_user(user.id)
        return self.update(user.id, ANY_OWNER, password=generate_password_hash(password))

    def set_role(self, name: str, role: Role, *, commit: bool = True) -> User:
        """Troca o papel de um usuário, pelo nome de login.

        Vale a partir da requisição seguinte, com o mesmo token — o papel não
        está assinado em lugar nenhum.
        """
        user = self._get_by_name(name)
        return self.update(user.id, ANY_OWNER, commit=commit, role=role)

    def _get_by_name(self, name: str) -> User:
        user = self.repository.get_by_name(name)
        if user is None:
            raise NotFoundError(f"Usuário '{name}' não encontrado.")
        return user

    @staticmethod
    def _check_password(password: str) -> None:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise BusinessRuleError(
                f"A senha deve ter de {MIN_PASSWORD_LENGTH} a {MAX_PASSWORD_LENGTH} caracteres."
            )
