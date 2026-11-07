"""Container de injeção de dependência.

Declara num lugar só como o grafo de objetos é montado (repository → service).
A rota recebe o service pronto via ``@inject`` + ``Provide[Container.x]``, o que
permite trocar qualquer peça por um dublê nos testes.
"""

from dependency_injector import containers, providers
from flask import Flask

from repository.sql.item_repository import ItemRepository
from repository.sql.user_repository import UserRepository
from repository.sql.vehicle_repository import VehicleRepository
from service.auth_service import AuthService
from service.item_service import ItemService
from service.user_service import UserService
from service.vehicle_service import VehicleService


class Container(containers.DeclarativeContainer):
    # Sem `wiring_config`: o wiring importa cada módulo que recebe, e um pacote
    # inteiro traria para o processo as rotas de outros perfis. O `init_app` liga
    # só as rotas do perfil.

    # --- Repositories ---------------------------------------------------------
    user_repository = providers.Factory(UserRepository)
    item_repository = providers.Factory(ItemRepository)
    vehicle_repository = providers.Factory(VehicleRepository)

    # --- Services -------------------------------------------------------------
    user_service = providers.Factory(UserService, repository=user_repository)
    auth_service = providers.Factory(AuthService, repository=user_repository)
    item_service = providers.Factory(ItemService, repository=item_repository)
    vehicle_service = providers.Factory(VehicleService, repository=vehicle_repository)


def init_app(app: Flask) -> None:
    container = Container()
    # Resolve os marcadores Provide[...] das rotas que este processo registra
    # (API_V1_ROUTES do perfil), e só delas.
    container.wire(modules=list(app.config["API_V1_ROUTES"]))
    # No app, para acesso em comandos CLI e testes
    # (app.container.item_service.override(...)).
    app.container = container
