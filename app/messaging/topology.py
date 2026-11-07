"""A topologia do RabbitMQ da telemetria, declarada num lugar só.

O job ``setup`` a aplica (``flask rabbitmq setup``) antes de os processos subirem,
pela API de gerenciamento e de forma idempotente. O producer e o consumer só a
usam: nenhum dos dois declara nada, e nenhum depende de o outro ter subido antes.
O endereço da mensagem (exchange e routing key) é do contrato; aqui ficam a fila
e o que a liga a ele.

A fila é *quorum*: replicada e durável, e o tipo não muda depois de criada. Os
limites (DLQ, número de entregas, tamanho) vão por policy, que muda sem recriar
a fila.
"""

from domain.contracts.telemetry_batch import EXCHANGE, ROUTING_KEY
from messaging.management import ManagementApi

#: A fila que o consumer drena para o bronze.
QUEUE = "bronze.telemetry"


def apply(api: ManagementApi, vhost: str) -> None:
    """Cria, ou mantém como está, o vhost, o exchange, a fila e o binding."""
    api.ensure_vhost(vhost)
    api.declare_exchange(vhost, EXCHANGE, kind="topic")
    api.declare_queue(vhost, QUEUE, arguments={"x-queue-type": "quorum"})
    api.bind(vhost, EXCHANGE, QUEUE, routing_key=ROUTING_KEY)
