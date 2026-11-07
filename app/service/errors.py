"""Exceções de domínio.

Cada condição tem seu tipo, mapeado para HTTP num lugar só
(``blueprint.http_errors.ERROR_STATUS``). Os projetos de origem reaproveitavam exceções
embutidas com significados trocados, e por isso cada rota tinha um try/except
gigante; aqui nenhuma tem.
"""


class ServiceError(Exception):
    """Raiz de todos os erros previstos da camada de serviço."""


class NotFoundError(ServiceError):
    """Recurso inexistente — ou existente, mas fora do escopo do solicitante."""


class ConflictError(ServiceError):
    """Viola uma restrição de unicidade."""


class BusinessRuleError(ServiceError):
    """Requisição bem formada, mas proibida pelas regras de negócio."""


class PermissionDeniedError(ServiceError):
    """Autenticado, porém sem autorização para a operação."""


class AuthenticationError(ServiceError):
    """Credenciais inválidas."""
