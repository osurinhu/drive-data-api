"""RabbitMQ: a configuração validada na subida, a sonda do broker e os comandos de operação.

Esta extensão não é dona de conexão. Os adaptadores de ``messaging/`` recebem a
URL e os prazos pelo container e abrem a própria conexão, depois do fork; aqui
ficam a validação, que reprova a subida com mensagem explícita, a sonda do
health e o grupo ``flask rabbitmq``.
"""

import logging

import click
from flask import Flask, current_app
from flask.cli import AppGroup

from instrumentation.probes import register_probe
from messaging import topology
from messaging.connection import broker_reachable, connection_parameters
from messaging.management import ManagementApi, ManagementApiError

rabbitmq_cli = AppGroup("rabbitmq", help="Operação do RabbitMQ.")


@rabbitmq_cli.command("setup")
def setup_command() -> None:
    """Aplica a topologia da telemetria no vhost da AMQP_URL (idempotente).

    Cria, ou mantém como estão, o vhost, o exchange, a fila e o binding, pela API
    de gerenciamento (AMQP_MANAGEMENT_URL). É o que o job `setup` roda.
    """
    management_url = str(current_app.config.get("AMQP_MANAGEMENT_URL") or "")
    if not management_url:
        raise click.ClickException(
            "AMQP_MANAGEMENT_URL não definida. Preencha FLASK_AMQP_MANAGEMENT_URL "
            "(http://usuario:senha@host:15672), ou rode via docker compose, no serviço setup."
        )
    timeout = float(current_app.config["AMQP_TIMEOUT_SECONDS"])
    try:
        api = ManagementApi(management_url, timeout=timeout)
    except ValueError as error:
        raise click.ClickException(f"AMQP_MANAGEMENT_URL inválida: {error}.") from None

    # A AMQP_URL já foi validada no init_app; dela saem o vhost e o usuário dos processos.
    amqp = connection_parameters(
        str(current_app.config["AMQP_URL"]),
        timeout=timeout,
        heartbeat=int(current_app.config["AMQP_HEARTBEAT_SECONDS"]),
        connection_name="setup",
    )
    vhost, user = amqp.virtual_host, amqp.credentials.username
    try:
        topology.apply(api, vhost)
        # Por enquanto o usuário da AMQP_URL recebe tudo no vhost. Os usuários por
        # processo, cada um com o mínimo, chegam com a segurança operacional.
        api.grant(vhost, user, configure=".*", write=".*", read=".*")
    except ManagementApiError as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Topologia aplicada no vhost '{vhost}' (fila '{topology.QUEUE}').")


def init_app(app: Flask) -> None:
    url = str(app.config.get("AMQP_URL") or "")
    if not url:
        raise RuntimeError(
            "AMQP_URL não definida. Preencha FLASK_AMQP_URL no .env "
            "(amqp://usuario:senha@host:5672/vhost), ou rode via docker compose, "
            "que monta a URL."
        )

    process = app.config.get("PROCESS") or "default"
    try:
        probe_parameters = connection_parameters(
            url,
            timeout=float(app.config["HEALTH_PROBE_TIMEOUT_SECONDS"]),
            heartbeat=int(app.config["AMQP_HEARTBEAT_SECONDS"]),
            connection_name=f"drive-data:{process}:health",
        )
    except ValueError as error:
        # As mensagens de connection_parameters são fixas e nunca repetem a URL,
        # que leva a senha.
        raise RuntimeError(f"AMQP_URL inválida: {error}.") from None

    # O pika registra em INFO cada passo de cada conexão. A sonda abre uma por
    # chamada do health, e isso afogaria o log; avisos e erros continuam saindo.
    logging.getLogger("pika").setLevel(logging.WARNING)

    register_probe(app, "rabbitmq", lambda: broker_reachable(probe_parameters))
    app.cli.add_command(rabbitmq_cli)
