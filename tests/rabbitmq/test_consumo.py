"""O consumidor da fila (``RabbitTelemetryConsumer``) contra o RabbitMQ de verdade.

O handler é um dublê: aqui se testa o destino de cada mensagem (ack, DLQ, de volta
para a fila) e a contagem de entregas da policy, não a gravação no bronze.
"""

import json

import pytest

from domain.contracts.telemetry_batch import encode
from domain.ports import BronzeUnavailable
from messaging.consumer import ConsumerFailed, RabbitTelemetryConsumer

PORTA_FECHADA = "amqp://ninguem:nada@127.0.0.1:1/teste"


@pytest.fixture(autouse=True)
def _fila_vazia(bronze_queue):
    """Todo teste daqui consome a fila: ela começa e termina vazia."""
    return bronze_queue


@pytest.fixture
def consumidor(amqp_url):
    """Fábrica de consumidores do vhost de testes, com pausa curta entre as falhas
    transitórias. O ``run(idle_timeout=1)`` drena a fila e para."""

    def _novo(handler) -> RabbitTelemetryConsumer:
        return RabbitTelemetryConsumer(
            amqp_url,
            timeout=5,
            heartbeat=60,
            connection_name="pytest:consumer",
            prefetch=20,
            handler=handler,
            retry_initial=0.01,
            retry_max=0.05,
        )

    return _novo


def test_o_lote_gravado_e_confirmado_e_sai_da_fila(
    consumidor, publicar, novo_lote, bronze_queue, dead_letters
):
    lote = novo_lote(registros=2)
    publicar(encode(lote))
    gravados = []

    consumidor(gravados.append).run(idle_timeout=1)

    assert gravados == [lote]
    assert bronze_queue() == []
    assert dead_letters() == []


@pytest.mark.parametrize(
    "corpo",
    [
        pytest.param(b"isto nao e json", id="não é JSON"),
        pytest.param(json.dumps({"schema_version": 2}).encode(), id="versão desconhecida"),
        pytest.param(json.dumps({"schema_version": 1}).encode(), id="envelope incompleto"),
    ],
)
def test_mensagem_fora_do_contrato_vai_para_a_dlq_sem_chegar_ao_handler(
    consumidor, publicar, dead_letters, corpo
):
    publicar(corpo, message_id="fora-do-contrato")
    chamadas = []

    consumidor(chamadas.append).run(idle_timeout=1)

    assert chamadas == []
    [(propriedades, morto)] = dead_letters(minimo=1)
    assert morto == corpo
    assert propriedades.message_id == "fora-do-contrato"
    assert propriedades.headers["x-first-death-reason"] == "rejected"


def test_bronze_fora_devolve_a_mensagem_sem_gastar_o_limite_de_entregas(
    consumidor, publicar, novo_lote, dead_letters, delivery_limit
):
    """A falha transitória volta com ``nack``, que não conta para o limite: o lote
    passa por mais tentativas do que o limite permitiria e ainda é gravado."""
    lote = novo_lote()
    publicar(encode(lote))
    falhas = delivery_limit + 2
    chamadas = []

    def bronze_que_volta(batch):
        chamadas.append(batch)
        if len(chamadas) <= falhas:
            raise BronzeUnavailable("Mongo fora (dublê)")

    consumidor(bronze_que_volta).run(idle_timeout=1)

    assert len(chamadas) == falhas + 1
    assert dead_letters() == []


def test_falha_inesperada_vai_para_a_dlq_depois_do_limite_de_entregas(
    consumidor, publicar, novo_lote, dead_letters, delivery_limit
):
    """A falha inesperada volta com ``reject``, que conta: a mensagem venenosa não
    trava a fila para sempre."""
    lote = novo_lote()
    publicar(encode(lote))
    chamadas = []

    def bug(batch):
        chamadas.append(batch)
        raise RuntimeError("bug (dublê)")

    consumidor(bug).run(idle_timeout=1)

    assert len(chamadas) == delivery_limit + 1
    [(propriedades, _)] = dead_letters(minimo=1)
    assert propriedades.message_id == "mensagem-do-teste"
    assert propriedades.headers["x-first-death-reason"] == "delivery_limit"


def test_parar_termina_o_lote_corrente_e_devolve_os_outros_sem_contar_falha(
    consumidor, publicar, novo_lote, bronze_queue
):
    for _ in range(5):
        publicar(encode(novo_lote()))
    gravados = []

    def grava_e_pede_para_parar(batch):
        gravados.append(batch)
        consumer.stop()

    consumer = consumidor(grava_e_pede_para_parar)
    # Quem para é o stop(). O idle_timeout é só a trava: sem ele, um handler que nunca
    # fosse chamado prenderia a suíte em vez de reprovar.
    consumer.run(idle_timeout=5)

    assert len(gravados) == 1
    restantes = bronze_queue(minimo=4)
    assert len(restantes) == 4
    # Devolvidas com nack: nenhuma entrega conta como falha.
    assert all((p.headers or {}).get("x-delivery-count", 0) == 0 for p, _ in restantes)


def test_o_consumidor_se_declara_consumindo_so_enquanto_inscrito(consumidor, publicar, novo_lote):
    publicar(encode(novo_lote()))
    durante = []
    consumer = consumidor(lambda _batch: durante.append(consumer.is_consuming()))
    assert consumer.is_consuming() is False

    consumer.run(idle_timeout=1)

    assert durante == [True]
    assert consumer.is_consuming() is False


def test_run_until_stopped_consome_e_volta_no_stop(consumidor, publicar, novo_lote):
    lote = novo_lote()
    publicar(encode(lote))
    gravados = []

    def grava_e_para(batch):
        gravados.append(batch)
        consumer.stop()

    consumer = consumidor(grava_e_para)

    consumer.run_until_stopped()

    assert gravados == [lote]


def test_broker_fora_do_ar_vira_consumer_failed():
    consumer = RabbitTelemetryConsumer(
        PORTA_FECHADA,
        timeout=2,
        heartbeat=60,
        connection_name="pytest:consumer",
        prefetch=1,
        handler=lambda _batch: None,
    )

    with pytest.raises(ConsumerFailed):
        consumer.run(idle_timeout=1)
