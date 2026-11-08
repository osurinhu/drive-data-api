"""A operação da DLQ contra o RabbitMQ de verdade: listar sem gastar entregas e
republicar sem perder nada."""

import time
from types import SimpleNamespace

import pika
import pytest

from domain.contracts.telemetry_batch import ROUTING_KEY, encode
from messaging.connection import connection_parameters
from messaging.consumer import RabbitTelemetryConsumer
from messaging.dead_letters import DeadLettersFailed, list_dead_letters, requeue_dead_letters
from messaging.topology import DEAD_LETTER_EXCHANGE, DEAD_LETTER_QUEUE

#: As duas filas começam e terminam vazias em todo teste daqui.
pytestmark = pytest.mark.usefixtures("dead_letters", "bronze_queue")


@pytest.fixture
def parametros(amqp_url):
    return connection_parameters(amqp_url, timeout=5, heartbeat=60, connection_name="pytest:dlq")


@pytest.fixture
def matar(parametros):
    """Põe uma mensagem na DLQ pelo exchange de mensagens mortas."""
    connection = pika.BlockingConnection(parametros)
    channel = connection.channel()
    channel.confirm_delivery()

    def _matar(message_id: str, routing_key: str = ROUTING_KEY) -> None:
        channel.basic_publish(
            exchange=DEAD_LETTER_EXCHANGE,
            routing_key=routing_key,
            body=b"corpo-morto",
            properties=pika.BasicProperties(
                message_id=message_id,
                delivery_mode=2,
                headers={"record_count": 3, "sender_user_id": 51},
            ),
            mandatory=True,
        )

    yield _matar
    connection.close()


@pytest.fixture
def matar_com_historia(parametros):
    """Põe na DLQ uma mensagem com os headers de quem já morreu duas vezes."""
    connection = pika.BlockingConnection(parametros)
    channel = connection.channel()
    channel.confirm_delivery()

    def _matar(message_id: str, *, primeira: str, ultima: str) -> None:
        channel.basic_publish(
            exchange=DEAD_LETTER_EXCHANGE,
            routing_key=ROUTING_KEY,
            body=b"corpo-morto",
            properties=pika.BasicProperties(
                message_id=message_id,
                headers={"x-first-death-reason": primeira, "x-last-death-reason": ultima},
            ),
            mandatory=True,
        )

    yield _matar
    connection.close()


def _listar_quando_chegarem(parametros, quantas):
    prazo = time.monotonic() + 5
    while True:
        cartas = list_dead_letters(parametros, limit=100)
        if len(cartas) >= quantas or time.monotonic() >= prazo:
            return cartas
        time.sleep(0.1)


def test_a_listagem_mostra_o_motivo_de_uma_mensagem_recusada_de_verdade(
    amqp_url, parametros, publicar, dead_letters
):
    assert dead_letters() == []
    publicar(b"isto nao e json", message_id="fora-do-contrato")
    RabbitTelemetryConsumer(
        amqp_url,
        timeout=5,
        heartbeat=60,
        connection_name="pytest:consumer",
        prefetch=1,
        handler=lambda _batch: None,
    ).run(idle_timeout=1)

    [carta] = _listar_quando_chegarem(parametros, 1)

    assert carta.message_id == "fora-do-contrato"
    assert carta.reason == "rejected"
    assert carta.origin_queue == "bronze.telemetry"


def test_a_listagem_traz_os_headers_de_inspecao(parametros, matar):
    matar("a")

    [carta] = _listar_quando_chegarem(parametros, 1)

    assert (carta.record_count, carta.sender_user_id) == (3, 51)


def test_listar_nao_tira_da_dlq_nem_gasta_entregas(parametros, matar, dead_letters):
    """A DLQ descarta a mensagem passado o limite de entregas: olhar não pode contar."""
    matar("a")
    matar("b")
    _listar_quando_chegarem(parametros, 2)

    for _ in range(3):
        list_dead_letters(parametros, limit=100)

    restantes = dead_letters(minimo=2)
    assert sorted(p.message_id for p, _ in restantes) == ["a", "b"]
    assert all((p.headers or {}).get("x-delivery-count", 0) == 0 for p, _ in restantes)


def test_a_listagem_respeita_o_limite(parametros, matar):
    for nome in "abc":
        matar(nome)
    _listar_quando_chegarem(parametros, 3)

    assert len(list_dead_letters(parametros, limit=2)) == 2


def test_dlq_vazia_lista_nada(parametros, dead_letters):
    assert dead_letters() == []

    assert list_dead_letters(parametros, limit=5) == []


def test_republicar_leva_a_mensagem_de_volta_a_fila_principal(
    parametros, matar, bronze_queue, dead_letters
):
    matar("a")
    _listar_quando_chegarem(parametros, 1)

    assert requeue_dead_letters(parametros, limit=10) == 1

    [(propriedades, corpo)] = bronze_queue(minimo=1)
    assert propriedades.message_id == "a"
    assert corpo == b"corpo-morto"
    assert dead_letters() == []


def test_republicar_respeita_o_limite(parametros, matar, bronze_queue, dead_letters):
    for nome in "abc":
        matar(nome)
    _listar_quando_chegarem(parametros, 3)

    assert requeue_dead_letters(parametros, limit=2) == 2

    assert len(bronze_queue(minimo=2)) == 2
    assert len(dead_letters(minimo=1)) == 1


def test_mensagem_sem_rota_para_a_republicacao_e_continua_na_dlq(
    parametros, matar, bronze_queue, dead_letters
):
    matar("sem-rota", routing_key="telemetry.batch.v0")
    _listar_quando_chegarem(parametros, 1)

    with pytest.raises(DeadLettersFailed, match="sem-rota"):
        requeue_dead_letters(parametros, limit=10)

    assert bronze_queue() == []
    assert [p.message_id for p, _ in dead_letters(minimo=1)] == ["sem-rota"]


def test_a_republicacao_para_no_tamanho_que_a_dlq_tinha(monkeypatch):
    """Com o consumer de pé, a mensagem ainda fora do contrato volta à DLQ na hora: o
    dublê entrega mensagens sem fim, como ela faria, e o limite alto não pode valer."""

    class Canal:
        publicadas = 0

        def confirm_delivery(self):
            pass

        def basic_get(self, _fila, auto_ack):
            assert auto_ack is False
            metodo = SimpleNamespace(delivery_tag=1, routing_key=ROUTING_KEY, message_count=1)
            return metodo, pika.BasicProperties(message_id="volta"), b"corpo"

        def basic_publish(self, **_kwargs):
            Canal.publicadas += 1

        def basic_ack(self, _tag):
            pass

    conexao = SimpleNamespace(channel=Canal, is_open=False)
    monkeypatch.setattr("messaging.dead_letters.pika.BlockingConnection", lambda _p: conexao)

    assert requeue_dead_letters(object(), limit=1000) == 2
    assert Canal.publicadas == 2


def test_cair_com_a_mensagem_pendente_nao_a_tira_da_dlq(parametros, matar, dead_letters):
    """A DLQ não tem DLX: passado o limite padrão do quorum (20), a mensagem sumiria."""
    matar("a")
    _listar_quando_chegarem(parametros, 1)

    for _ in range(22):
        conexao = pika.BlockingConnection(parametros)
        canal = conexao.channel()
        assert canal.basic_get(DEAD_LETTER_QUEUE, auto_ack=False)[0] is not None
        conexao.close()

    assert [p.message_id for p, _ in dead_letters(minimo=1)] == ["a"]


def test_a_listagem_mostra_o_motivo_da_ultima_morte(parametros, matar_com_historia):
    matar_com_historia("a", primeira="rejected", ultima="delivery_limit")

    [carta] = _listar_quando_chegarem(parametros, 1)

    assert carta.reason == "delivery_limit"


def test_a_mensagem_republicada_ganha_um_limite_de_entregas_novo(
    amqp_url, parametros, publicar, novo_lote, bronze_queue, dead_letters, delivery_limit
):
    """Republicar é dar outra chance: a mensagem não pode voltar à DLQ na primeira
    falha por herdar a contagem de quando morreu."""
    assert bronze_queue() == dead_letters() == []
    publicar(encode(novo_lote()))
    chamadas = []

    def bug(batch):
        chamadas.append(batch)
        raise RuntimeError("bug (dublê)")

    def consumir():
        RabbitTelemetryConsumer(
            amqp_url,
            timeout=5,
            heartbeat=60,
            connection_name="pytest:consumer",
            prefetch=1,
            handler=bug,
        ).run(idle_timeout=1)

    consumir()
    _listar_quando_chegarem(parametros, 1)
    assert len(chamadas) == delivery_limit + 1

    assert requeue_dead_letters(parametros, limit=1) == 1
    consumir()

    assert len(chamadas) == 2 * (delivery_limit + 1)
    assert len(dead_letters(minimo=1)) == 1


def test_broker_fora_do_ar_vira_dead_letters_failed():
    parametros = connection_parameters(
        "amqp://ninguem:nada@127.0.0.1:1/teste", timeout=2, heartbeat=60, connection_name="pytest"
    )

    with pytest.raises(DeadLettersFailed):
        list_dead_letters(parametros, limit=1)
