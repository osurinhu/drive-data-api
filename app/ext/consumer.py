"""O processo consumer: ``flask consumer run``.

Um comando da CLI do Flask, e não um script avulso: o consumer reaproveita a
configuração por perfil, os logs e as extensões (``EXTENSIONS`` do ``[consumer]``),
como os processos HTTP. O grafo de objetos vem do ``app.consumer_container``.

O comando roda até receber SIGTERM (o ``docker compose stop``) ou SIGINT (Ctrl+C):
o sinal só pede a parada, e o consumidor termina a mensagem corrente antes de
fechar. No container, o processo é o PID 1, que o kernel não deixa morrer pelo
SIGTERM sem um handler: sem ele, o ``stop`` esperaria os 10 s e mataria.
"""

import signal
from types import FrameType

import click
from flask import Flask, current_app
from flask.cli import AppGroup

from messaging.consumer import ConsumerFailed

consumer_cli = AppGroup("consumer", help="O consumer da fila da telemetria.")


@consumer_cli.command("run")
def run_command() -> None:
    """Consome a fila bronze.telemetry e grava no bronze, até SIGTERM ou SIGINT."""
    consumer = current_app.consumer_container.telemetry_consumer()

    def _parar(_signum: int, _frame: FrameType | None) -> None:
        consumer.stop()

    for sinal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sinal, _parar)

    click.echo("Consumindo a fila bronze.telemetry (SIGTERM ou Ctrl+C para parar)...")
    try:
        consumer.run()
    except ConsumerFailed as error:
        # Sai com erro: no compose, o `restart` sobe o processo de novo.
        raise click.ClickException(f"o consumo parou: {error}") from error
    click.echo("Consumer parado.")


def init_app(app: Flask) -> None:
    app.cli.add_command(consumer_cli)
