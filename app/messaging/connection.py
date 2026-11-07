"""Parâmetros de conexão AMQP e a sonda de disponibilidade do broker.

Os parâmetros saem de um lugar só para que o producer, o consumer, os comandos
de operação e a sonda usem os mesmos prazos e apareçam no painel do RabbitMQ
com um nome que diz de que processo cada conexão é.
"""

import contextlib
import logging

import pika
from pika.exceptions import AMQPError

logger = logging.getLogger(__name__)

_SCHEMES = ("amqp://", "amqps://")


def connection_parameters(
    url: str, *, timeout: float, heartbeat: int, connection_name: str
) -> pika.URLParameters:
    """Parâmetros da URL com os prazos do projeto.

    Uma tentativa só, sem espera entre tentativas: quem decide repetir é quem
    chama. Os prazos do settings.toml valem sobre os da query string da URL.
    Levanta ``ValueError`` se a URL não for AMQP.
    """
    if not url.startswith(_SCHEMES):
        raise ValueError("a URL não usa o esquema amqp:// (ou amqps://)")

    try:
        parameters = pika.URLParameters(url)
    except ValueError:
        # A mensagem do urllib repete trechos da URL: uma senha com "/" sem
        # codificação vira "porta" nela. O `from None` também tira o erro
        # original do traceback.
        raise ValueError(
            "a URL não pôde ser lida (confira host, porta, vhost e parâmetros; usuário "
            "e senha com caracteres como / ? # @ precisam de codificação)"
        ) from None
    parameters.socket_timeout = timeout
    parameters.stack_timeout = timeout
    parameters.blocked_connection_timeout = timeout
    parameters.heartbeat = heartbeat
    parameters.connection_attempts = 1
    parameters.retry_delay = 0
    parameters.client_properties = {"connection_name": connection_name}
    return parameters


def broker_reachable(parameters: pika.ConnectionParameters) -> bool:
    """Abre e fecha uma conexão: o broker atende, aceita a credencial e o vhost existe.

    Uma conexão nova por chamada, sem estado guardado: é segura entre threads e
    não deixa conexão ociosa para o broker derrubar por falta de heartbeat. O
    custo é um handshake AMQP por chamada, desprezível na frequência de um health.
    """
    try:
        connection = pika.BlockingConnection(parameters)
    except (AMQPError, OSError) as error:
        logger.warning("Sonda do RabbitMQ: sem resposta (%.300s)", describe_error(error))
        return False

    with contextlib.suppress(AMQPError, OSError):
        connection.close()
    return True


def describe_error(error: BaseException) -> str:
    """Tipo e motivo de um erro do pika, para log.

    O ``AMQPConnectionError`` tem ``str()`` vazio: o motivo (conexão recusada,
    prazo esgotado) fica nos ``args``, como o erro de socket de cada tentativa.
    Nenhum dos dois leva a credencial da URL.
    """
    motivo = str(error) or ", ".join(str(arg) for arg in error.args)
    return f"{type(error).__name__}: {motivo}"
