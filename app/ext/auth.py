"""Autenticação (quem é) e autorização (o que pode).

Dois pontos que convém saber antes de mexer:

* A claim ``sub`` carrega **apenas o id do usuário, como string** — exigência da
  RFC 7519, validada pelas versões recentes do Flask-JWT-Extended.
* O **papel não viaja no token**: é lido de ``current_user.role``, da linha que o
  ``user_lookup_loader`` já carregou nesta requisição.

O porquê de cada um está em `documentacao/02-decisoes.md`, seção Segurança.
"""

from functools import wraps

from flask import Flask, g
from flask_jwt_extended import JWTManager, current_user, verify_jwt_in_request

from blueprint.http_errors import error_response
from domain.authz import ANY_OWNER, OwnerScope, Permission, has_permission
from repository.sql.user_repository import UserRepository
from service.errors import PermissionDeniedError

jwt = JWTManager()


# --- Serialização da identidade ----------------------------------------------


@jwt.user_identity_loader
def _user_identity(user) -> str:
    """Converte o objeto User na claim ``sub`` do token."""
    return str(user.id)


@jwt.user_lookup_loader
def _user_lookup(_jwt_header: dict, jwt_payload: dict):
    """Reidrata o User a partir do token, exposto como ``current_user``.

    Custa **um SELECT por requisição autenticada**: o Flask-JWT-Extended chama
    este loader em todo ``verify_jwt_in_request()``. Preço deliberado — é o que
    faz um usuário excluído, ou rebaixado, perder o acesso na hora.
    """
    return UserRepository().get_by_id(int(jwt_payload["sub"]))


# --- Normalização dos erros de token -----------------------------------------
# Os parâmetros com prefixo `_` são impostos pela assinatura que o
# Flask-JWT-Extended espera; não são usados aqui. As mensagens não dizem "de acesso"
# nem "de renovação": valem para os dois tipos de token.


@jwt.unauthorized_loader
def _missing_token(_reason: str):
    return error_response(401, "Token ausente.")


@jwt.invalid_token_loader
def _invalid_token(_reason: str):
    return error_response(401, "Token inválido.")


@jwt.expired_token_loader
def _expired_token(_jwt_header: dict, _jwt_payload: dict):
    return error_response(401, "Token expirado.")


@jwt.user_lookup_error_loader
def _user_not_found(_jwt_header: dict, _jwt_payload: dict):
    return error_response(401, "Usuário do token não existe mais.")


# --- Autorização --------------------------------------------------------------


def _verificar_token_uma_vez() -> None:
    """Valida o token no máximo uma vez por requisição.

    ``permission_required`` aparece duas vezes sempre que um método exige mais
    que o piso da classe, e cada verificação recarregaria o usuário. A marca é
    nossa, não um atributo interno da biblioteca, e vive no contexto de aplicação
    (``flask.g``) — que em atendimento HTTP dura exatamente uma requisição.
    """
    if not g.get("_token_verificado", False):
        verify_jwt_in_request()
        g._token_verificado = True


def permission_required(*permissions: Permission):
    """Exige token e **todas** as permissões informadas.

    Sem argumentos, exige apenas o token — use em rota que qualquer usuário
    autenticado pode acessar.

    Na lista ``decorators`` da classe, declara o piso do recurso; no método,
    exige mais que esse piso. Método novo sem decorator herda o piso da classe:
    esquecer fecha, não abre.
    """

    exigidas = frozenset(permissions)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            _verificar_token_uma_vez()
            if not has_permission(current_user.role, *exigidas):
                # 403 pelo mapa de `blueprint.http_errors`: falta de permissão para a
                # OPERAÇÃO, decidida antes de qualquer consulta. Recurso de outro
                # dono responde 404 (ver `BaseService._assert_ownership`).
                raise PermissionDeniedError("Seu perfil não permite esta operação.")
            return fn(*args, **kwargs)

        wrapper.__permissoes__ = exigidas
        return wrapper

    # A marca também no decorator, e não só na função embrulhada: é assim que
    # `tests/test_contracts.py` a encontra na lista `decorators` da classe.
    decorator.__permissoes__ = exigidas
    return decorator


#: Piso mínimo: exige token e nada mais. Use em rota que qualquer usuário
#: autenticado pode acessar.
authenticated = permission_required()


def refresh_token_required(fn):
    """Exige o token de renovação no header ``Authorization``, e não o de acesso.

    Só renovar e encerrar a sessão o aceitam: nas demais rotas, o token de renovação é
    recusado. O que ele ainda vale (revogado, já trocado) quem decide é o ``TokenService``,
    na mesma transação da troca; aqui só se confere a assinatura, a validade e o usuário.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request(refresh=True)
        return fn(*args, **kwargs)

    # A marca que o `tests/test_contracts.py` procura em toda rota protegida.
    wrapper.__permissoes__ = frozenset()
    wrapper.__token_de_renovacao__ = True
    return wrapper


def current_owner_scope(permission: Permission) -> OwnerScope:
    """Escopo de ownership desta requisição: ``ANY_OWNER`` se o papel do usuário
    tiver a permissão de escopo informada, senão o id dele.

    É o único ponto onde o filtro de posse é dispensado, e a permissão vem
    explícita no ponto de chamada para que a rota diga *de quem* ela opera.
    """
    if has_permission(current_user.role, permission):
        return ANY_OWNER
    return current_user.id


def init_app(app: Flask) -> None:
    if not app.config.get("JWT_SECRET_KEY"):
        raise RuntimeError(
            "JWT_SECRET_KEY não definida. Copie .env.example para .env e preencha JWT_SECRET_KEY."
        )
    jwt.init_app(app)
