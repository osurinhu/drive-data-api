"""Camada de API: instância do flask-smorest e prefixo da versão."""

from flask import Flask
from flask_smorest import Api

from ext.config import load_routes

SECURITY_SCHEME = "bearerAuth"

#: Marcador para rotas públicas: ``@blp.doc(security=PUBLIC)``. A exigência de
#: token é declarada **globalmente** no spec (abaixo), então toda operação
#: começa fechada e só as públicas abrem mão — esquecer de anotar erra para o
#: lado seguro.
PUBLIC: list = []

api = Api(spec_kwargs={"security": [{SECURITY_SCHEME: []}]})

#: O prefixo de versão: os módulos de ``API_V1_ROUTES`` são servidos abaixo dele
#: (ver ``ext.config.load_routes``).
V1_URL_PREFIX = "/api/v1"


def init_app(app: Flask) -> None:
    # API_TITLE, API_VERSION, OPENAPI_* vêm do settings.toml via app.config.
    api.init_app(app)

    # Declara o esquema Bearer para que o botão "Authorize" apareça no Swagger.
    api.spec.components.security_scheme(
        SECURITY_SCHEME,
        {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    )

    load_routes(app, api, V1_URL_PREFIX, "API_V1_ROUTES")
