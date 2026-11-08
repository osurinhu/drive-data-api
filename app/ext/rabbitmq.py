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
from messaging.connection import AmqpParameters, broker_reachable, connection_parameters
from messaging.dead_letters import DeadLettersFailed, list_dead_letters, requeue_dead_letters
from messaging.management import ManagementApi, ManagementApiError

rabbitmq_cli = AppGroup("rabbitmq", help="Operação do RabbitMQ.")


@rabbitmq_cli.command("setup")
def setup_command() -> None:
    """Aplica a topologia da telemetria no vhost da AMQP_URL (idempotente).

    Cria, ou mantém como estão, o vhost, os exchanges, a fila, a DLQ e os bindings,
    aplica a policy da fila e os usuários do producer e do consumer, pela API de
    gerenciamento (AMQP_MANAGEMENT_URL). É o que o job `setup` roda.
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
    producer = _process_user("AMQP_PRODUCER_USER", "AMQP_PRODUCER_PASSWORD")
    consumer = _process_user("AMQP_CONSUMER_USER", "AMQP_CONSUMER_PASSWORD")

    # Do mesmo endereço saem o vhost e o usuário de operação.
    amqp = _operation_parameters("setup")
    vhost, user = amqp.virtual_host, amqp.credentials.username
    try:
        topology.apply(
            api,
            vhost,
            delivery_limit=int(current_app.config["AMQP_DELIVERY_LIMIT"]),
            max_bytes=int(current_app.config["AMQP_QUEUE_MAX_BYTES"]),
        )
        topology.apply_users(api, vhost, producer=producer, consumer=consumer)
        # O usuário de operação (o do job setup e dos comandos da DLQ) recebe tudo no
        # vhost, que pode ter acabado de nascer.
        api.grant(vhost, user, configure=".*", write=".*", read=".*")
    except ManagementApiError as error:
        raise click.ClickException(str(error)) from error

    click.echo(
        f"Topologia aplicada no vhost '{vhost}' "
        f"(fila '{topology.QUEUE}', DLQ '{topology.DEAD_LETTER_QUEUE}')."
    )


@rabbitmq_cli.command("dlq-list")
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
def dlq_list_command(limit: int) -> None:
    """Mostra as mensagens da DLQ, que continuam lá."""
    try:
        letters = list_dead_letters(_operation_parameters("dlq-list"), limit=limit)
    except DeadLettersFailed as error:
        raise click.ClickException(f"a DLQ não pôde ser lida: {error}") from error
    if not letters:
        click.echo("A DLQ está vazia.")
    for letter in letters:
        click.echo(
            f"{letter.message_id}  motivo={letter.reason}  fila={letter.origin_queue}  "
            f"registros={letter.record_count}  remetente={letter.sender_user_id}"
        )


@rabbitmq_cli.command("dlq-requeue")
@click.option("--limit", type=click.IntRange(min=1), required=True)
def dlq_requeue_command(limit: int) -> None:
    """Republica mensagens da DLQ na fila principal, depois de corrigida a causa."""
    try:
        requeued = requeue_dead_letters(_operation_parameters("dlq-requeue"), limit=limit)
    except DeadLettersFailed as error:
        raise click.ClickException(f"a republicação parou: {error}") from error
    click.echo(f"{requeued} mensagem(ns) republicada(s).")


def _process_user(user_key: str, password_key: str) -> topology.BrokerUser:
    """O usuário de um processo, com a senha obrigatória: o `setup` não cria usuário sem ela."""
    password = str(current_app.config.get(password_key) or "")
    if not password:
        raise click.ClickException(
            f"{password_key} não definida. Preencha FLASK_{password_key}, ou rode via "
            "docker compose, no serviço setup."
        )
    return topology.BrokerUser(str(current_app.config[user_key]), password)


def _operation_parameters(name: str) -> AmqpParameters:
    """Os parâmetros da AMQP_URL (já validada no init_app) para um comando de operação."""
    return connection_parameters(
        str(current_app.config["AMQP_URL"]),
        timeout=float(current_app.config["AMQP_TIMEOUT_SECONDS"]),
        heartbeat=int(current_app.config["AMQP_HEARTBEAT_SECONDS"]),
        connection_name=name,
    )


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

    # O pika registra em INFO cada passo de cada conexão, e com traceback cada queda: com
    # a sonda abrindo uma por chamada e o broker fora, isso afogaria o log. A falha de
    # conexão ou de canal chega aos adaptadores como exceção, e são eles que a registram.
    logging.getLogger("pika").setLevel(logging.CRITICAL)

    register_probe(app, "rabbitmq", lambda: broker_reachable(probe_parameters))
    app.cli.add_command(rabbitmq_cli)
