"""A DLQ da telemetria, para a operação: listar e republicar as mensagens mortas.

A DLQ não tem consumidor: é fila de ação operacional. Quem a usa é o grupo
``flask rabbitmq`` (``dlq-list`` e ``dlq-requeue``).

Ler uma fila é tirar a mensagem dela: a listagem devolve o que leu com ``nack``, que
não gasta entrega (RabbitMQ 4.3+). A
republicação confirma a mensagem na DLQ só depois de o broker confirmar a
publicação: uma queda entre as duas deixa a mensagem nas duas filas, e o consumer
grava cada registro uma vez só, pelo id.
"""

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPError, NackError, UnroutableError

from domain.contracts.telemetry_batch import EXCHANGE
from messaging.connection import describe_error
from messaging.topology import DEAD_LETTER_QUEUE


class DeadLettersFailed(RuntimeError):
    """A operação na DLQ não terminou. A mensagem em andamento continua na DLQ."""


@dataclass(frozen=True)
class DeadLetter:
    """O que se vê de uma mensagem morta sem abrir o corpo."""

    message_id: str | None
    reason: str | None
    origin_queue: str | None
    record_count: int | None
    sender_user_id: int | None


@contextlib.contextmanager
def _open_channel(parameters: pika.ConnectionParameters) -> Iterator[BlockingChannel]:
    try:
        connection = pika.BlockingConnection(parameters)
    except (AMQPError, OSError) as error:
        raise DeadLettersFailed(describe_error(error)) from error
    try:
        yield connection.channel()
    except (AMQPError, OSError) as error:
        raise DeadLettersFailed(describe_error(error)) from error
    finally:
        if connection.is_open:
            with contextlib.suppress(AMQPError, OSError):
                connection.close()


def list_dead_letters(parameters: pika.ConnectionParameters, *, limit: int) -> list[DeadLetter]:
    """Até ``limit`` mensagens da DLQ, que continuam lá."""
    found: list[DeadLetter] = []
    with _open_channel(parameters) as channel:
        tags: list[int] = []
        while len(found) < limit:
            method, properties, _body = channel.basic_get(DEAD_LETTER_QUEUE, auto_ack=False)
            if method is None:
                break
            tags.append(method.delivery_tag)
            found.append(_describe(properties))
        for tag in tags:
            channel.basic_nack(tag, requeue=True)
    return found


def requeue_dead_letters(parameters: pika.ConnectionParameters, *, limit: int) -> int:
    """Republica até ``limit`` mensagens da DLQ no endereço do contrato e devolve quantas.

    Vale o tamanho da DLQ na primeira leitura: com o consumer de pé, a mensagem ainda
    fora do contrato volta para a DLQ em milissegundos, e ler até o ``limit`` a
    republicaria de novo. Levanta ``DeadLettersFailed`` na primeira que o broker não
    aceitar. Ela continua na DLQ, e as anteriores já foram republicadas.
    """
    requeued = 0
    with _open_channel(parameters) as channel:
        channel.confirm_delivery()
        while requeued < limit:
            method, properties, body = channel.basic_get(DEAD_LETTER_QUEUE, auto_ack=False)
            if method is None:
                break
            if requeued == 0:
                # O `message_count` é o que sobrou na fila depois desta.
                limit = min(limit, method.message_count + 1)
            try:
                # A mensagem morta mantém a routing key de origem.
                channel.basic_publish(
                    exchange=EXCHANGE,
                    routing_key=method.routing_key,
                    body=body,
                    properties=properties,
                    mandatory=True,
                )
            except (NackError, UnroutableError) as error:
                channel.basic_nack(method.delivery_tag, requeue=True)
                raise DeadLettersFailed(
                    f"a mensagem {properties.message_id} continua na DLQ "
                    f"({type(error).__name__}), depois de {requeued} republicada(s)."
                ) from error
            channel.basic_ack(method.delivery_tag)
            requeued += 1
    return requeued


def _describe(properties: pika.BasicProperties) -> DeadLetter:
    headers = properties.headers or {}
    return DeadLetter(
        message_id=properties.message_id,
        # A última morte: a primeira ficaria com o motivo de antes de uma republicação.
        reason=headers.get("x-last-death-reason"),
        origin_queue=headers.get("x-last-death-queue"),
        record_count=headers.get("record_count"),
        sender_user_id=headers.get("sender_user_id"),
    )
