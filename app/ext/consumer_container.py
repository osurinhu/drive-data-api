"""Container de injeção de dependência do consumer (``app.consumer_container``).

O ``ext/container.py`` serve os processos HTTP e importa o lado deles inteiro (o
SQL, o publisher, os serviços de cadastro). O consumer tem este container, só com
o que ele usa: o adaptador do bronze, o serviço que grava e o consumidor da fila.
Assim o processo consumer não carrega o SQLAlchemy nem o JWT
(``tests/unit/test_composicao.py`` confere).
"""

from dependency_injector import containers, providers
from flask import Flask

from messaging.consumer import RabbitTelemetryConsumer
from repository.mongo.bronze_writer import MongoBronzeWriter
from service.telemetry.bronze_ingest_service import BronzeIngestService


class ConsumerContainer(containers.DeclarativeContainer):
    #: Os valores do settings.toml que os adaptadores recebem no construtor
    #: (preenchidos pelo `init_app`).
    config = providers.Configuration()

    bronze_writer = providers.Factory(MongoBronzeWriter, timeout=config.bronze.write_timeout)
    bronze_ingest_service = providers.Factory(BronzeIngestService, writer=bronze_writer)

    # Factory: o consumidor guarda a conexão do próprio `run`, e cada `flask consumer
    # run` é um processo.
    telemetry_consumer = providers.Factory(
        RabbitTelemetryConsumer,
        url=config.amqp.url,
        timeout=config.amqp.timeout,
        heartbeat=config.amqp.heartbeat,
        connection_name=config.amqp.consumer_connection_name,
        prefetch=config.amqp.prefetch,
        handler=bronze_ingest_service.provided.ingest,
    )


def init_app(app: Flask) -> None:
    container = ConsumerContainer()
    process = app.config.get("PROCESS") or "default"
    container.config.from_dict(
        {
            "amqp": {
                "url": str(app.config.get("AMQP_URL") or ""),
                "timeout": float(app.config["AMQP_TIMEOUT_SECONDS"]),
                "heartbeat": int(app.config["AMQP_HEARTBEAT_SECONDS"]),
                "prefetch": int(app.config["AMQP_PREFETCH"]),
                "consumer_connection_name": f"drive-data:{process}:consumer",
            },
            "bronze": {"write_timeout": float(app.config["BRONZE_WRITE_TIMEOUT_SECONDS"])},
        }
    )
    # No app, para o comando `flask consumer run` e para os testes
    # (app.consumer_container.bronze_writer.override(...)).
    app.consumer_container = container
