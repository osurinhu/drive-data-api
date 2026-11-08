"""A topologia do RabbitMQ da telemetria, declarada num lugar só.

O job ``setup`` a aplica (``flask rabbitmq setup``) antes de os processos subirem,
pela API de gerenciamento e de forma idempotente. O producer e o consumer só a
usam: nenhum dos dois declara nada, e nenhum depende de o outro ter subido antes.
O endereço da mensagem (exchange e routing key) é do contrato; aqui ficam as filas
e o que as liga a ele.

As filas são *quorum*: replicadas e duráveis, e o tipo não muda depois de criadas.
O comportamento da fila principal (para onde vai a mensagem morta, quantas
entregas ela aguenta, quanto ela guarda) vem de uma policy, que muda sem recriar a
fila.

Mensagem morta é a que o consumer recusou por estar fora do contrato, ou a que
passou do limite de entregas sem ser gravada. Ela vai para o ``telemetry.dlx`` e
dele para a DLQ, que não tem consumidor: é fila de ação operacional.
"""

import re

from domain.contracts.telemetry_batch import EXCHANGE, ROUTING_KEY
from messaging.management import ManagementApi

#: A fila que o consumer drena para o bronze.
QUEUE = "bronze.telemetry"
#: Para onde vão as mensagens mortas da fila principal.
DEAD_LETTER_EXCHANGE = "telemetry.dlx"
DEAD_LETTER_QUEUE = "bronze.telemetry.dlq"
#: A policy da fila principal.
POLICY = "bronze-telemetry"
#: A policy da DLQ.
DEAD_LETTER_POLICY = "bronze-telemetry-dlq"


def apply(api: ManagementApi, vhost: str, *, delivery_limit: int, max_bytes: int) -> None:
    """Cria, ou mantém como estão, o vhost, os exchanges, as filas e os bindings, e
    aplica a policy da fila principal."""
    api.ensure_vhost(vhost)
    api.declare_exchange(vhost, EXCHANGE, kind="topic")
    api.declare_exchange(vhost, DEAD_LETTER_EXCHANGE, kind="topic")
    api.declare_queue(vhost, QUEUE, arguments={"x-queue-type": "quorum"})
    api.declare_queue(vhost, DEAD_LETTER_QUEUE, arguments={"x-queue-type": "quorum"})
    api.bind(vhost, EXCHANGE, QUEUE, routing_key=ROUTING_KEY)
    # A mensagem morta mantém a routing key de origem: o "#" recebe todas.
    api.bind(vhost, DEAD_LETTER_EXCHANGE, DEAD_LETTER_QUEUE, routing_key="#")
    api.put_policy(
        vhost,
        POLICY,
        pattern=f"^{re.escape(QUEUE)}$",
        definition={
            "dead-letter-exchange": DEAD_LETTER_EXCHANGE,
            # A mensagem só sai da fila depois que a DLQ a confirma. Exige
            # `reject-publish`: com o `drop-head` padrão, o RabbitMQ volta ao
            # at-most-once, e a mensagem morta pode se perder no caminho.
            "dead-letter-strategy": "at-least-once",
            "overflow": "reject-publish",
            # A contrapressão: cheia, a fila recusa a publicação com `nack`, e o
            # producer responde 503. Não se perde o que já foi aceito.
            "max-length-bytes": max_bytes,
            # Conta só as entregas que falharam (o `reject` do consumer, a queda da
            # conexão com a mensagem pendente). O `nack` da falha transitória não
            # conta (RabbitMQ 4.3+).
            "delivery-limit": delivery_limit,
        },
    )
    # A DLQ não tem DLX: com o limite padrão do quorum (20), quem a lê e cai com a
    # mensagem pendente a faria sumir. O -1 desliga o limite.
    api.put_policy(
        vhost,
        DEAD_LETTER_POLICY,
        pattern=f"^{re.escape(DEAD_LETTER_QUEUE)}$",
        definition={"delivery-limit": -1},
    )
