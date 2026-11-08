"""O processo consumer: ``flask consumer run``.

Um comando da CLI do Flask, e não um script avulso: o consumer reaproveita a
configuração por perfil, os logs e as extensões (``EXTENSIONS`` do ``[consumer]``),
como os processos HTTP. O grafo de objetos vem do ``app.consumer_container``.

O comando roda até receber SIGTERM (o ``docker compose stop``) ou SIGINT (Ctrl+C):
o sinal só pede a parada, e o consumidor termina a mensagem corrente antes de
fechar. No container, o processo é o PID 1, que o kernel não deixa morrer pelo
SIGTERM sem um handler: sem ele, o ``stop`` esperaria os 10 s e mataria.

Enquanto consome, o processo responde, em ``CONSUMER_HEALTH_PORT``, se está vivo e pronto. O
broker fora não o encerra: ele fica de pé, não pronto, e reconecta.
"""

import signal
from types import FrameType

import click
from flask import Flask, current_app
from flask.cli import AppGroup

from instrumentation.consumer_health import ConsumerHealthServer
from instrumentation.probes import registered_probes

consumer_cli = AppGroup("consumer", help="O consumer da fila da telemetria.")


@consumer_cli.command("run")
def run_command() -> None:
    """Consome a fila bronze.telemetry e grava no bronze, até SIGTERM ou SIGINT."""
    consumer = current_app.consumer_container.telemetry_consumer()

    def _parar(_signum: int, _frame: FrameType | None) -> None:
        consumer.stop()

    for sinal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sinal, _parar)

    port = int(current_app.config["CONSUMER_HEALTH_PORT"])
    health = ConsumerHealthServer(
        port,
        consuming=consumer.is_consuming,
        bronze_ok=registered_probes(current_app)["mongo"],
    )
    try:
        health.start()
    except OSError as error:
        raise click.ClickException(f"a porta de saúde {port} não abriu: {error}") from error

    click.echo("Consumindo a fila bronze.telemetry (SIGTERM ou Ctrl+C para parar)...")
    try:
        consumer.run_until_stopped()
    finally:
        health.stop()
    click.echo("Consumer parado.")


def init_app(app: Flask) -> None:
    app.cli.add_command(consumer_cli)
