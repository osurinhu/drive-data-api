"""A topologia e o publisher da telemetria contra o RabbitMQ de verdade (vhost de testes)."""

import pytest
from pika.exceptions import StreamLostError

import messaging.publisher
from domain.contracts.telemetry_batch import decode
from domain.ports import PublishFailed
from messaging import topology
from messaging.management import ManagementApiError
from messaging.publisher import APP_ID, RabbitTelemetryPublisher

PORTA_FECHADA = "amqp://ninguem:nada@127.0.0.1:1/teste"


@pytest.fixture
def publicador(amqp_url):
    publisher = RabbitTelemetryPublisher(
        amqp_url, timeout=5, heartbeat=60, connection_name="pytest:publisher"
    )
    yield publisher
    publisher._discard()


# --- Topologia --------------------------------------------------------------------


def test_aplicar_a_topologia_de_novo_nao_muda_nada(amqp_management, amqp_topology, delivery_limit):
    """O setup roda a cada `docker compose up`: aplicar sobre o que existe é um no-op."""
    topology.apply(amqp_management, amqp_topology, delivery_limit=delivery_limit)


def test_a_fila_e_quorum(amqp_management, amqp_topology):
    """A prova é a recusa: declarar a mesma fila com outro tipo dá 400, porque o tipo
    não muda depois da criação. O motivo vem na mensagem, sem a credencial."""
    with pytest.raises(ManagementApiError, match="inequivalent") as erro:
        amqp_management.declare_queue(
            amqp_topology, topology.QUEUE, arguments={"x-queue-type": "classic"}
        )

    assert "400" in str(erro.value)


# --- Publisher --------------------------------------------------------------------


def test_o_lote_chega_na_fila_com_as_propriedades_do_contrato(publicador, bronze_queue, novo_lote):
    lote = novo_lote(registros=3)

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


def test_a_conexao_e_reaproveitada_entre_publicacoes(publicador, bronze_queue, novo_lote):
    publicador.publish(novo_lote())
    conexao = publicador._connection

    publicador.publish(novo_lote())

    assert publicador._connection is conexao
    assert len(bronze_queue()) == 2


def test_sem_fila_para_a_routing_key_a_publicacao_falha(
    publicador, bronze_queue, novo_lote, monkeypatch
):
    """``mandatory``: sem binding, o broker devolve a mensagem em vez de descartá-la."""
    monkeypatch.setattr(messaging.publisher, "ROUTING_KEY", "telemetry.batch.v0")

    with pytest.raises(PublishFailed, match="UnroutableError"):
        publicador.publish(novo_lote())

    assert bronze_queue() == []


def test_broker_fora_do_ar_vira_publish_failed(novo_lote):
    publisher = RabbitTelemetryPublisher(
        PORTA_FECHADA, timeout=2, heartbeat=60, connection_name="pytest:publisher"
    )

    with pytest.raises(PublishFailed):
        publisher.publish(novo_lote())


def test_conexao_reaproveitada_que_caiu_e_reaberta_uma_vez(
    publicador, bronze_queue, novo_lote, monkeypatch
):
    """O broker derruba a conexão ociosa (heartbeat), e o worker só descobre ao
    publicar. A primeira tentativa falha como falharia o socket fechado; a segunda,
    numa conexão nova, entrega."""
    publicador.publish(novo_lote())
    canal_velho = publicador._channel

    def socket_fechado(**_kwargs):
        raise StreamLostError("conexão derrubada pelo broker")

    monkeypatch.setattr(canal_velho, "basic_publish", socket_fechado)
    lote = novo_lote()

    publicador.publish(lote)

    assert publicador._channel is not canal_velho
    assert [decode(corpo).message_id for _, corpo in bronze_queue()][-1] == lote.message_id


def test_conexao_nova_que_falha_nao_e_repetida(novo_lote, monkeypatch):
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
        publisher.publish(novo_lote())

    assert len(tentativas) == 1
