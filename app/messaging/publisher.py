"""Publicação dos lotes de telemetria no RabbitMQ: o adaptador do ``TelemetryPublisher``.

Uma conexão por processo, aberta na primeira publicação (depois do fork do
gunicorn) e reaproveitada nas seguintes. Cada publicação:

* espera a confirmação do broker (*publisher confirms*): voltar sem erro quer
  dizer que a fila guardou a mensagem;
* vai com ``mandatory``: sem fila ligada à routing key, o broker devolve a
  mensagem e a publicação falha, em vez de sumir calada;
* é persistente (``delivery_mode=2``).

A ``BlockingConnection`` só atende o heartbeat quando é usada: num worker ocioso, o
broker derruba a conexão, e a publicação seguinte encontra o socket fechado. Por
isso a conexão reaproveitada que falha é reaberta uma vez, e a publicação,
repetida. Repetir é seguro: o consumer grava cada registro pelo id. A conexão que
acabou de abrir não é repetida, porque aí o broker está mesmo fora.
"""

import contextlib
import logging
import os
import threading

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPError, NackError, UnroutableError

from domain.contracts.telemetry_batch import (
    CONTENT_ENCODING,
    CONTENT_TYPE,
    EXCHANGE,
    ROUTING_KEY,
    TelemetryBatchV1,
    encode,
)
from domain.ports import PublishFailed
from messaging.connection import connection_parameters, describe_error

logger = logging.getLogger(__name__)

#: A propriedade ``app_id`` das mensagens: quem as publicou.
APP_ID = "drive-data-api"


def message_properties(batch: TelemetryBatchV1) -> pika.BasicProperties:
    """As propriedades AMQP do envelope. Os headers repetem o que permite inspecionar
    a mensagem (no painel, na DLQ) sem abrir o corpo."""
    return pika.BasicProperties(
        content_type=CONTENT_TYPE,
        content_encoding=CONTENT_ENCODING,
        delivery_mode=pika.DeliveryMode.Persistent,
        message_id=str(batch.message_id),
        correlation_id=batch.request_id,
        timestamp=int(batch.received_at.timestamp()),
        type=ROUTING_KEY,
        app_id=APP_ID,
        headers={
            "schema_version": batch.schema_version,
            "sender_user_id": batch.sender.user_id,
            "record_count": len(batch.records),
        },
    )


class RabbitTelemetryPublisher:
    """Publica com confirmação, numa conexão do processo.

    Uma instância por processo (``providers.Singleton`` no container). O lock
    serializa as publicações das threads do mesmo processo: a conexão do pika não
    é segura entre threads, e o servidor de desenvolvimento do Flask atende cada
    requisição numa thread.
    """

    def __init__(self, url: str, *, timeout: float, heartbeat: int, connection_name: str) -> None:
        self._parameters = connection_parameters(
            url, timeout=timeout, heartbeat=heartbeat, connection_name=connection_name
        )
        self._lock = threading.Lock()
        self._pid: int | None = None
        self._connection: pika.BlockingConnection | None = None
        self._channel: BlockingChannel | None = None

    def publish(self, batch: TelemetryBatchV1) -> None:
        body = encode(batch)
        properties = message_properties(batch)
        with self._lock:
            reused = self._channel is not None and self._pid == os.getpid()
            try:
                self._send(body, properties)
                return
            except (NackError, UnroutableError) as error:
                # O broker respondeu, e a resposta foi não: repetir daria o mesmo.
                raise self._failed(batch, error) from error
            except (AMQPError, OSError) as error:
                self._discard()
                if not reused:
                    raise self._failed(batch, error) from error
                logger.info(
                    "Conexão de publicação perdida (%s); reabrindo uma vez.", describe_error(error)
                )
            try:
                self._send(body, properties)
            except (AMQPError, OSError) as error:
                self._discard()
                raise self._failed(batch, error) from error

    def _send(self, body: bytes, properties: pika.BasicProperties) -> None:
        self._channel_of_this_process().basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=body,
            properties=properties,
            mandatory=True,
        )

    def _channel_of_this_process(self) -> BlockingChannel:
        if self._pid != os.getpid():
            # A conexão herdada do processo pai (um fork) é dele: não se fecha nem se usa.
            self._connection = self._channel = None
        if self._channel is None or not self._channel.is_open:
            self._discard()
            self._connection = pika.BlockingConnection(self._parameters)
            channel = self._connection.channel()
            channel.confirm_delivery()
            self._channel = channel
            self._pid = os.getpid()
        return self._channel

    def _discard(self) -> None:
        connection, self._connection, self._channel = self._connection, None, None
        if connection is not None and connection.is_open:
            with contextlib.suppress(AMQPError, OSError):
                connection.close()

    @staticmethod
    def _failed(batch: TelemetryBatchV1, error: BaseException) -> PublishFailed:
        logger.warning("Lote %s não publicado: %.300s", batch.message_id, describe_error(error))
        return PublishFailed(type(error).__name__)
