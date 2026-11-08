"""Rotas de autenticação."""

from datetime import UTC, datetime
from typing import NamedTuple

from dependency_injector.wiring import Provide, inject
from flask import current_app
from flask.views import MethodView
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    current_user,
    decode_token,
    get_jwt,
)
from flask_smorest import Blueprint

from blueprint.http_errors import error_responses
from ext.api import PUBLIC
from ext.auth import authenticated, refresh_token_required
from ext.container import Container
from schemas.auth_schema import LoginSchema, TokenPairSchema, UserSchema
from service.auth_service import AuthService
from service.token_service import TokenService

blp = Blueprint(
    "auth",
    __name__,
    url_prefix="/auth",
    description="Autenticação e identidade do usuário",
)


class _NewPair(NamedTuple):
    """O par recém-emitido: o corpo da resposta e o que o `TokenService` registra."""

    body: dict
    refresh_jti: str
    refresh_expires_at: datetime


def _issue_pair(user, *, fresh: bool) -> _NewPair:
    # Os tokens carregam só o `sub` (o id, via user_identity_loader). O papel **não**
    # entra em claim nenhuma: ele é lido do banco a cada requisição, para que uma
    # troca de papel valha na hora — ver ext/auth.py.
    access_token = create_access_token(identity=user, fresh=fresh)
    refresh_token = create_refresh_token(identity=user)
    claims = decode_token(refresh_token)
    return _NewPair(
        body={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": int(current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]),
            "refresh_expires_in": int(current_app.config["JWT_REFRESH_TOKEN_EXPIRES"]),
        },
        refresh_jti=claims["jti"],
        refresh_expires_at=datetime.fromtimestamp(claims["exp"], UTC),
    )


class TokenView(MethodView):
    """Base das rotas que registram ou revogam tokens de renovação."""

    @inject
    def __init__(self, token_service: TokenService = Provide[Container.token_service]) -> None:
        self.token_service = token_service


@blp.route("/token")
class LoginResource(TokenView):
    @inject
    def __init__(self, auth_service: AuthService = Provide[Container.auth_service]) -> None:
        super().__init__()
        self.auth_service = auth_service

    @blp.doc(security=PUBLIC)
    @blp.arguments(LoginSchema)
    @blp.response(200, TokenPairSchema)
    @error_responses(blp, 401)
    def post(self, payload: dict):
        """Autentica e devolve um par de tokens.

        Use o token de acesso no header `Authorization: Bearer <token>`. Quando ele
        expirar, troque o de renovação por um par novo em `/auth/refresh`.
        """
        user = self.auth_service.authenticate(payload["name"], payload["password"])
        pair = _issue_pair(user, fresh=True)
        self.token_service.register(user.id, pair.refresh_jti, pair.refresh_expires_at)
        return pair.body


@blp.route("/refresh")
class RefreshResource(TokenView):
    @refresh_token_required
    @blp.response(200, TokenPairSchema)
    def post(self):
        """Troca o token de renovação por um par novo.

        Envie o **token de renovação** no header `Authorization: Bearer <token>`. Cada um
        vale para uma única troca: reapresentar um token já trocado encerra a sessão, e
        os dois lados precisam fazer login de novo.
        """
        pair = _issue_pair(current_user, fresh=False)
        self.token_service.rotate(
            get_jwt()["jti"], new_jti=pair.refresh_jti, expires_at=pair.refresh_expires_at
        )
        return pair.body


@blp.route("/logout")
class LogoutResource(TokenView):
    @refresh_token_required
    @blp.response(204)
    def post(self):
        """Encerra a sessão.

        Envie o **token de renovação** no header `Authorization: Bearer <token>`. Ele
        e os demais da mesma sessão deixam de valer. O token de acesso já emitido
        continua válido até expirar, em no máximo 15 minutos.
        """
        self.token_service.revoke_family(get_jwt()["jti"])


@blp.route("/me")
class MeResource(MethodView):
    decorators = [authenticated]

    @blp.response(200, UserSchema)
    def get(self):
        """Dados do usuário autenticado, com o papel e as permissões dele."""
        return current_user
