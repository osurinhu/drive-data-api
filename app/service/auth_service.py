"""Verificação de credenciais.

Não estende BaseService porque não faz CRUD: só consulta e compara hash.
A emissão do token em si fica na rota, que é quem conhece o protocolo HTTP.
"""

from werkzeug.security import check_password_hash

from domain.models import User
from repository.sql.user_repository import UserRepository
from service.errors import AuthenticationError


class AuthService:
    def __init__(self, repository: UserRepository) -> None:
        self.repository = repository

    def authenticate(self, name: str, password: str) -> User:
        user = self.repository.get_by_name(name)

        # Mesma mensagem para "usuário não existe" e "senha errada": responder
        # de forma diferente permitiria descobrir quais nomes estão cadastrados.
        if user is None or not check_password_hash(user.password, password):
            raise AuthenticationError("Credenciais inválidas.")

        return user
