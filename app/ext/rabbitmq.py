"""RabbitMQ: a configuração validada na subida e a sonda de disponibilidade do broker.

Esta extensão não é dona de conexão. Os adaptadores de ``messaging/`` recebem a
URL e os prazos pelo container e abrem a própria conexão, depois do fork; aqui
ficam a validação, que reprova a subida com mensagem explícita, e a sonda do
health. Os comandos ``flask rabbitmq ...`` chegam com a topologia.
"""

import logging

from flask import Flask

from instrumentation.probes import register_probe
from messaging.connection import broker_reachable, connection_parameters


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
