"""Container de injeção de dependência dos processos HTTP (o api e o bronze-api).

Declara num lugar só como o grafo de objetos é montado (repository → service, e
os adaptadores, como o publisher e o leitor do bronze, → os serviços do pipeline).
A rota recebe o service pronto via ``@inject`` + ``Provide[Container.x]``, o que
permite trocar qualquer peça por um dublê nos testes.

Os dois processos declaram o grafo inteiro, mas cada um só cria o que as rotas do
seu perfil pedem: o api nunca cria o leitor do bronze, e o bronze-api nunca cria o
publisher. Quem conecta a cada serviço externo são as extensões do perfil
(``EXTENSIONS``).
"""

from dependency_injector import containers, providers
from flask import Flask

from messaging.publisher import RabbitTelemetryPublisher
from repository.mongo.bronze_reader import MongoBronzeReader
from repository.sql.item_repository import ItemRepository
from repository.sql.user_repository import UserRepository
from repository.sql.vehicle_driver_repository import VehicleDriverRepository
from repository.sql.vehicle_repository import VehicleRepository
from service.auth_service import AuthService
from service.item_service import ItemService
from service.telemetry.bronze_read_service import BronzeReadService
from service.telemetry.ingest_service import TelemetryIngestService
from service.user_service import UserService
from service.vehicle_driver_service import VehicleDriverService
from service.vehicle_service import VehicleService


class Container(containers.DeclarativeContainer):
    # Sem `wiring_config`: o wiring importa cada módulo que recebe, e um pacote
    # inteiro traria para o processo as rotas de outros perfis. O `init_app` liga
    # só as rotas do perfil.

    #: Os valores do settings.toml que os adaptadores recebem no construtor
    #: (preenchidos pelo `init_app`).
    config = providers.Configuration()

    # --- Repositories ---------------------------------------------------------
    user_repository = providers.Factory(UserRepository)
    item_repository = providers.Factory(ItemRepository)
    vehicle_repository = providers.Factory(VehicleRepository)
    vehicle_driver_repository = providers.Factory(VehicleDriverRepository)

    # --- Mensageria -----------------------------------------------------------
    # Singleton: uma conexão por processo, aberta na primeira publicação. Só o
    # processo que serve a rota de telemetria chega a criá-lo.
    telemetry_publisher = providers.Singleton(
        RabbitTelemetryPublisher,
        url=config.amqp.url,
        timeout=config.amqp.timeout,
        heartbeat=config.amqp.heartbeat,
        connection_name=config.amqp.publisher_connection_name,
    )

    # --- Bronze (a leitura da silver) -------------------------------------------
    # Factory: o leitor não guarda conexão; o cliente é do `ext.mongo`, um por processo.
    bronze_reader = providers.Factory(MongoBronzeReader, timeout=config.bronze.read_timeout)

    # --- Services -------------------------------------------------------------
    user_service = providers.Factory(UserService, repository=user_repository)
    auth_service = providers.Factory(AuthService, repository=user_repository)
    item_service = providers.Factory(ItemService, repository=item_repository)
    vehicle_service = providers.Factory(
        VehicleService, repository=vehicle_repository, drivers=vehicle_driver_repository
    )
    # O sub-recurso recebe o serviço do pai: é por ele que a posse do veículo vale
    # para os motoristas (o 404 do veículo de outro dono).
    vehicle_driver_service = providers.Factory(
        VehicleDriverService,
        repository=vehicle_driver_repository,
        vehicles=vehicle_service,
        users=user_repository,
    )
    telemetry_ingest_service = providers.Factory(
        TelemetryIngestService,
        authorization=vehicle_repository,
        publisher=telemetry_publisher,
        retry_after_seconds=config.ingest.retry_after_seconds,
    )
    bronze_read_service = providers.Factory(
        BronzeReadService, reader=bronze_reader, settle_seconds=config.bronze.settle_seconds
    )


def init_app(app: Flask) -> None:
    container = Container()
    process = app.config.get("PROCESS") or "default"
    container.config.from_dict(
        {
            "amqp": {
                "url": str(app.config.get("AMQP_URL") or ""),
                "timeout": float(app.config["AMQP_TIMEOUT_SECONDS"]),
                "heartbeat": int(app.config["AMQP_HEARTBEAT_SECONDS"]),
                "publisher_connection_name": f"drive-data:{process}:publisher",
            },
            "ingest": {"retry_after_seconds": int(app.config["INGEST_RETRY_AFTER_SECONDS"])},
            "bronze": {
                "read_timeout": float(app.config["BRONZE_READ_TIMEOUT_SECONDS"]),
                "settle_seconds": float(app.config["BRONZE_SETTLE_SECONDS"]),
            },
        }
    )
    # Resolve os marcadores Provide[...] das rotas que este processo registra
    # (API_V1_ROUTES do perfil), e só delas.
    container.wire(modules=list(app.config["API_V1_ROUTES"]))
    # No app, para acesso em comandos CLI e testes
    # (app.container.item_service.override(...)).
    app.container = container
