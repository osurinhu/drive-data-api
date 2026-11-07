"""A topologia e o publisher da telemetria contra o RabbitMQ de verdade (vhost de testes)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pika.exceptions import StreamLostError

import messaging.publisher
from domain.contracts.telemetry_batch import (
    Sender,
    TelemetryBatchV1,
    TelemetryRecordV1,
    decode,
)
from domain.ports import PublishFailed
from messaging import topology
from messaging.management import ManagementApiError
from messaging.publisher import APP_ID, RabbitTelemetryPublisher

PORTA_FECHADA = "amqp://ninguem:nada@127.0.0.1:1/teste"


def _lote(registros: int = 1) -> TelemetryBatchV1:
    agora = datetime.now(UTC)
    return TelemetryBatchV1(
        message_id=uuid4(),
        received_at=agora,
        request_id="req-do-teste",
        sender=Sender(user_id=51),
        records=tuple(
            TelemetryRecordV1(
                record_id=uuid4(),
                vin="93YBSR7RHEJ298116",
                vehicle_id=7,
                sender_relation="owner",
                collected_at=agora,
                payload_user_matches_sender=True,
                raw={"n": n},
            )
            for n in range(registros)
        ),
    )


@pytest.fixture
def publicador(amqp_url):
    publisher = RabbitTelemetryPublisher(
        amqp_url, timeout=5, heartbeat=60, connection_name="pytest:publisher"
    )
    yield publisher
    publisher._discard()


# --- Topologia --------------------------------------------------------------------


def test_aplicar_a_topologia_de_novo_nao_muda_nada(amqp_management, amqp_topology):
    """O setup roda a cada `docker compose up`: aplicar sobre o que existe é um no-op."""
    topology.apply(amqp_management, amqp_topology)


def test_a_fila_e_quorum(amqp_management, amqp_topology):
    """A prova é a recusa: declarar a mesma fila com outro tipo dá 400, porque o tipo
    não muda depois da criação. O motivo vem na mensagem, sem a credencial."""
    with pytest.raises(ManagementApiError, match="inequivalent") as erro:
        amqp_management.declare_queue(
            amqp_topology, topology.QUEUE, arguments={"x-queue-type": "classic"}
        )

    assert "400" in str(erro.value)


# --- Publisher --------------------------------------------------------------------


def test_o_lote_chega_na_fila_com_as_propriedades_do_contrato(publicador, bronze_queue):
    lote = _lote(registros=3)

    publicador.publish(lote)

    [(propriedades, corpo)] = bronze_queue()
    assert decode(corpo) == lote
    assert propriedades.content_type == "application/json"
    assert propriedades.content_encoding == "utf-8"
    assert propriedades.delivery_mode == 2
    assert propriedades.message_id == str(lote.message_id)
    assert propriedades.correlation_id == "req-do-teste"
    assert propriedades.timestamp == int(lote.received_at.timestamp())
    assert propriedades.type == "telemetry.batch.v1"
    assert propriedades.app_id == APP_ID
    assert propriedades.headers == {"schema_version": 1, "sender_user_id": 51, "record_count": 3}


def test_a_conexao_e_reaproveitada_entre_publicacoes(publicador, bronze_queue):
    publicador.publish(_lote())
    conexao = publicador._connection

    publicador.publish(_lote())

    assert publicador._connection is conexao
    assert len(bronze_queue()) == 2


def test_sem_fila_para_a_routing_key_a_publicacao_falha(publicador, bronze_queue, monkeypatch):
    """``mandatory``: sem binding, o broker devolve a mensagem em vez de descartá-la."""
    monkeypatch.setattr(messaging.publisher, "ROUTING_KEY", "telemetry.batch.v0")

    with pytest.raises(PublishFailed, match="UnroutableError"):
        publicador.publish(_lote())

    assert bronze_queue() == []


def test_broker_fora_do_ar_vira_publish_failed():
    publisher = RabbitTelemetryPublisher(
        PORTA_FECHADA, timeout=2, heartbeat=60, connection_name="pytest:publisher"
    )

    with pytest.raises(PublishFailed):
        publisher.publish(_lote())


def test_conexao_reaproveitada_que_caiu_e_reaberta_uma_vez(publicador, bronze_queue, monkeypatch):
    """O broker derruba a conexão ociosa (heartbeat), e o worker só descobre ao
    publicar. A primeira tentativa falha como falharia o socket fechado; a segunda,
    numa conexão nova, entrega."""
    publicador.publish(_lote())
    canal_velho = publicador._channel

    def socket_fechado(**_kwargs):
        raise StreamLostError("conexão derrubada pelo broker")

    monkeypatch.setattr(canal_velho, "basic_publish", socket_fechado)
    lote = _lote()

    publicador.publish(lote)

    assert publicador._channel is not canal_velho
    assert [decode(corpo).message_id for _, corpo in bronze_queue()][-1] == lote.message_id


def test_conexao_nova_que_falha_nao_e_repetida(monkeypatch):
    """Se a conexão acabou de abrir e falhou, o broker está fora: repetir só dobraria
    a espera do app."""
    tentativas = []

    def conexao_recusada(_parametros):
        tentativas.append(1)
        raise OSError("connection refused")

    monkeypatch.setattr(messaging.publisher.pika, "BlockingConnection", conexao_recusada)
    publisher = RabbitTelemetryPublisher(
        PORTA_FECHADA, timeout=2, heartbeat=60, connection_name="pytest:publisher"
    )

    with pytest.raises(PublishFailed):
        publisher.publish(_lote())

    assert len(tentativas) == 1
