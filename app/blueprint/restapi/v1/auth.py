"""Rotas de autenticação."""

from dependency_injector.wiring import Provide, inject
from flask import current_app
from flask.views import MethodView
from flask_jwt_extended import create_access_token, current_user
from flask_smorest import Blueprint

from blueprint.http_errors import error_responses
from ext.api import PUBLIC
from ext.auth import authenticated
from ext.container import Container
from schemas.auth_schema import LoginSchema, TokenSchema, UserSchema
from service.auth_service import AuthService

blp = Blueprint(
    "auth",
    __name__,
    url_prefix="/auth",
    description="Autenticação e identidade do usuário",
)


@blp.route("/token")
class TokenResource(MethodView):
    @inject
    def __init__(self, auth_service: AuthService = Provide[Container.auth_service]) -> None:
        self.auth_service = auth_service

    @blp.doc(security=PUBLIC)
    @blp.arguments(LoginSchema)
    @blp.response(200, TokenSchema)
    @error_responses(blp, 401)
    def post(self, payload: dict):
        """Autentica e devolve um token de acesso.

        Use o token no header `Authorization: Bearer <token>`.
        """
        user = self.auth_service.authenticate(payload["name"], payload["password"])

        # O token carrega só o `sub` (o id, via user_identity_loader). O papel
        # **não** entra em claim nenhuma: ele é lido do banco a cada requisição,
        # para que uma troca de papel valha na hora — ver ext/auth.py.
        token = create_access_token(identity=user)
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": int(current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]),
        }


@blp.route("/me")
class MeResource(MethodView):
    decorators = [authenticated]

    @blp.response(200, UserSchema)
    def get(self):
        """Dados do usuário autenticado, com o papel e as permissões dele."""
        return current_user
