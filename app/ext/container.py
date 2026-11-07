"""Container de injeção de dependência.

Declara num lugar só como o grafo de objetos é montado (repository → service).
A rota recebe o service pronto via ``@inject`` + ``Provide[Container.x]``, o que
permite trocar qualquer peça por um dublê nos testes.
"""

from dependency_injector import containers, providers
from flask import Flask

from repository.item_repository import ItemRepository
from repository.user_repository import UserRepository
from service.auth_service import AuthService
from service.item_service import ItemService
from service.user_service import UserService


class Container(containers.DeclarativeContainer):
    # Pacotes onde os marcadores Provide[...] devem ser resolvidos.
    wiring_config = containers.WiringConfiguration(packages=["blueprint.restapi.v1"])

    # --- Repositories ---------------------------------------------------------
    user_repository = providers.Factory(UserRepository)
    item_repository = providers.Factory(ItemRepository)

    # --- Services -------------------------------------------------------------
    user_service = providers.Factory(UserService, repository=user_repository)
    auth_service = providers.Factory(AuthService, repository=user_repository)
    item_service = providers.Factory(ItemService, repository=item_repository)


def init_app(app: Flask) -> None:
    # No app, para acesso em comandos CLI e testes
    # (app.container.item_service.override(...)).
    app.container = Container()
