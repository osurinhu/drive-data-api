"""Cadastro público de usuários. Só o processo `api` o serve: a silver não se cadastra."""

from dependency_injector.wiring import Provide, inject
from flask.views import MethodView
from flask_smorest import Blueprint

from blueprint.http_errors import error_responses
from ext.api import PUBLIC
from ext.container import Container
from schemas.auth_schema import RegistrationSchema, UserSchema
from service.user_service import UserService

blp = Blueprint(
    "registration",
    __name__,
    url_prefix="/auth",
    description="Cadastro público de usuários",
)


@blp.route("/register")
class RegistrationResource(MethodView):
    @inject
    def __init__(self, user_service: UserService = Provide[Container.user_service]) -> None:
        self.user_service = user_service

    @blp.doc(security=PUBLIC)
    @blp.arguments(RegistrationSchema)
    @blp.response(201, UserSchema)
    @error_responses(blp, 409)
    def post(self, payload: dict):
        """Cria uma conta de usuário.

        A conta nasce com o papel `usuario`; outro papel só é atribuído pela linha de comando.
        """
        # Sem `role`, de propósito: é o que impede escalar privilégio por HTTP.
        return self.user_service.register(name=payload["name"], password=payload["password"])
