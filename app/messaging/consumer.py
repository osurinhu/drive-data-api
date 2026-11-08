"""Consumo da fila do bronze: o lado do RabbitMQ do consumer.

Recebe cada envelope, decodifica pelo contrato e o entrega ao handler (o serviço
que grava no bronze). A resposta ao broker diz o destino da mensagem:

* gravou: ``basic_ack``;
* fora do contrato (``ContractError``): ``basic_reject(requeue=False)``, e a policy
  da fila a leva para a DLQ. Repetir daria o mesmo resultado;
* bronze fora (``BronzeUnavailable``): ``basic_nack(requeue=True)`` e uma pausa que
  dobra a cada falha seguida. No RabbitMQ 4.3+, o ``nack`` não conta para o
  ``delivery-limit``: a mensagem espera o bronze voltar, sem ir para a DLQ;
* qualquer outra exceção (um bug, um documento que o Mongo recusa):
  ``basic_reject(requeue=True)``, que conta. Passado o ``delivery-limit`` da policy,
  a mensagem vai para a DLQ em vez de travar a fila.

Uma conexão e um canal, com ``prefetch`` mensagens em voo e ack manual. ``stop()``
pode ser chamado de um handler de sinal, porque só levanta uma marca: o laço
termina a mensagem corrente, devolve as outras à fila com ``nack`` (que não conta
como falha) e fecha.
"""

import contextlib
import logging
import time
from collections.abc import Callable

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPError
from pika.spec import Basic, BasicProperties

from domain.contracts.telemetry_batch import ContractError, TelemetryBatchV1, decode
from domain.ports import BronzeUnavailable
from messaging.connection import connection_parameters, describe_error
from messaging.topology import QUEUE

logger = logging.getLogger(__name__)

#: Quem grava o lote. Volta sem erro quando gravou; levanta ``BronzeUnavailable``
#: quando o bronze não responde.
Handler = Callable[[TelemetryBatchV1], object]


class ConsumerFailed(RuntimeError):
    """O consumo parou por causa do broker: a conexão não abriu ou caiu, ou a fila não
    existe. O que estava em voo, sem ack, volta para a fila."""


class RabbitTelemetryConsumer:
    def __init__(
        self,
        url: str,
        *,
        timeout: float,
        heartbeat: int,
        connection_name: str,
        prefetch: int,
        handler: Handler,
        retry_initial: float = 0.5,
        retry_max: float = 30.0,
    ) -> None:
        self._parameters = connection_parameters(
            url, timeout=timeout, heartbeat=heartbeat, connection_name=connection_name
        )
        self._prefetch = prefetch
        self._handler = handler
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._stopping = False
        #: Falhas transitórias seguidas: a pausa dobra a cada uma, até ``retry_max``.
        self._failures = 0
        self._last_activity = 0.0
        self._connection: pika.BlockingConnection | None = None

    def stop(self) -> None:
        """Pede o fim do consumo. Seguro num handler de sinal."""
        self._stopping = True

    def run(self, *, idle_timeout: float | None = None) -> None:
        """Consome até ``stop()``. Com ``idle_timeout``, para também depois de tantos
        segundos sem mensagem: é o "drene a fila" dos testes. Levanta
        ``ConsumerFailed``."""
        try:
            connection = pika.BlockingConnection(self._parameters)
        except (AMQPError, OSError) as error:
            raise ConsumerFailed(describe_error(error)) from error

        self._connection = connection
        tick = 1.0 if idle_timeout is None else min(1.0, idle_timeout)
        try:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=self._prefetch)
            consumer_tag = channel.basic_consume(QUEUE, self._on_message, auto_ack=False)
            self._last_activity = time.monotonic()
            while not self._stopping and not self._idle(idle_timeout):
                connection.process_data_events(time_limit=tick)
            # O pika devolve com nack o que já chegou e não foi entregue ao handler.
            channel.basic_cancel(consumer_tag)
        except (AMQPError, OSError) as error:
            raise ConsumerFailed(describe_error(error)) from error
        finally:
            self._connection = None
            if connection.is_open:
                with contextlib.suppress(AMQPError, OSError):
                    connection.close()

    def _idle(self, idle_timeout: float | None) -> bool:
        return idle_timeout is not None and time.monotonic() - self._last_activity >= idle_timeout

    def _on_message(
        self,
        channel: BlockingChannel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
    ) -> None:
        tag = method.delivery_tag
        if self._stopping:
            # Chegou no mesmo lote de eventos do pedido de parada: volta sem contar.
            channel.basic_nack(tag, requeue=True)
            return
        try:
            self._handle(channel, tag, properties, body)
        finally:
            self._last_activity = time.monotonic()

    def _handle(
        self, channel: BlockingChannel, tag: int, properties: BasicProperties, body: bytes
    ) -> None:
        try:
            batch = decode(body)
        except ContractError as error:
            logger.warning(
                "Mensagem %s fora do contrato, enviada à DLQ: %.300s", properties.message_id, error
            )
            channel.basic_reject(tag, requeue=False)
            return

        try:
            self._handler(batch)
        except BronzeUnavailable as error:
            channel.basic_nack(tag, requeue=True)
            self._pause(batch, error)
            return
        except Exception:
            logger.exception(
                "Falha inesperada ao gravar o lote %s: ele volta para a fila e conta para o "
                "limite de entregas (passado o limite, vai para a DLQ).",
                batch.message_id,
            )
            channel.basic_reject(tag, requeue=True)
            return

        channel.basic_ack(tag)
        self._failures = 0

    def _pause(self, batch: TelemetryBatchV1, error: BronzeUnavailable) -> None:
        """Espera antes da próxima mensagem, sem perder o heartbeat: ``connection.sleep``
        continua atendendo o broker, e o ``time.sleep`` deixaria a conexão cair."""
        self._failures += 1
        delay = min(self._retry_max, self._retry_initial * 2 ** min(self._failures - 1, 16))
        logger.warning(
            "Bronze indisponível (%s): o lote %s volta para a fila; nova tentativa em %.1f s.",
            error,
            batch.message_id,
            delay,
        )
        assert self._connection is not None
        deadline = time.monotonic() + delay
        while not self._stopping and (remaining := deadline - time.monotonic()) > 0:
            self._connection.sleep(min(1.0, remaining))
