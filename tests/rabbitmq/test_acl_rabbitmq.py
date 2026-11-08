"""As permissões dos usuários por processo, contra o RabbitMQ de verdade.

Os dois lados valem: cada processo não alcança o que não é dele, e os adaptadores de
verdade funcionam só com o que receberam. Um ``queue_declare`` novo no consumer, por
exemplo, passaria nos outros testes, que usam a credencial de operação.
"""

import contextlib
import secrets
from types import SimpleNamespace
from urllib.parse import urlsplit

import pika
import pytest
from pika.exceptions import AMQPConnectionError, AMQPError, ChannelClosedByBroker

from domain.contracts.telemetry_batch import EXCHANGE, ROUTING_KEY, encode
from dubles import ObservadorEmMemoria
from messaging import topology
from messaging.connection import connection_parameters
from messaging.consumer import RabbitTelemetryConsumer
from messaging.management import ManagementApi, ManagementApiError
from messaging.publisher import RabbitTelemetryPublisher

#: O usuário é do broker, e não do vhost: com os nomes de verdade, a suíte trocaria a
#: senha dos processos do ambiente de desenvolvimento.
PRODUTOR = "teste_acl_producer"
CONSUMIDOR = "teste_acl_consumer"

SEM_PERMISSAO = 403

pytestmark = pytest.mark.usefixtures("bronze_queue", "dead_letters")


def _senha() -> str:
    return secrets.token_urlsafe(16)


def _com_credencial(url: str, usuario: topology.BrokerUser) -> str:
    partes = urlsplit(url)
    endereco = partes.netloc.rpartition("@")[2]
    return f"{partes.scheme}://{usuario.name}:{usuario.password}@{endereco}{partes.path}"


@pytest.fixture(scope="module")
def usuarios(amqp_management, amqp_topology):
    produtor = topology.BrokerUser(PRODUTOR, _senha())
    consumidor = topology.BrokerUser(CONSUMIDOR, _senha())
    topology.apply_users(amqp_management, amqp_topology, producer=produtor, consumer=consumidor)
    return SimpleNamespace(produtor=produtor, consumidor=consumidor)


def _conectar(amqp_url: str, usuario: topology.BrokerUser) -> pika.BlockingConnection:
    return pika.BlockingConnection(
        connection_parameters(
            _com_credencial(amqp_url, usuario),
            timeout=5,
            heartbeat=60,
            connection_name="pytest:acl",
        )
    )


@pytest.fixture
def canal(amqp_url):
    """Abre um canal com a credencial do usuário."""
    conexoes = []

    def _abrir(usuario: topology.BrokerUser):
        conexao = _conectar(amqp_url, usuario)
        conexoes.append(conexao)
        return conexao.channel()

    yield _abrir
    for conexao in conexoes:
        with contextlib.suppress(AMQPError, OSError):
            conexao.close()


def _publicar_em(exchange: str):
    def _agir(canal_aberto):
        canal_aberto.confirm_delivery()
        canal_aberto.basic_publish(exchange=exchange, routing_key=ROUTING_KEY, body=b"x")

    return _agir


def _ler_a_fila(canal_aberto) -> None:
    canal_aberto.basic_get(topology.QUEUE, auto_ack=True)


def _ler_a_dlq(canal_aberto) -> None:
    canal_aberto.basic_get(topology.DEAD_LETTER_QUEUE, auto_ack=True)


def _declarar_fila(canal_aberto) -> None:
    canal_aberto.queue_declare("teste-acl", durable=True)


def _apagar_a_fila(canal_aberto) -> None:
    canal_aberto.queue_delete(topology.QUEUE)


@pytest.mark.parametrize(
    ("quem", "agir"),
    [
        pytest.param("produtor", _ler_a_fila, id="o producer não lê a fila"),
        pytest.param("produtor", _ler_a_dlq, id="o producer não lê a DLQ"),
        pytest.param(
            "produtor",
            _publicar_em(topology.DEAD_LETTER_EXCHANGE),
            id="o producer não publica na DLX",
        ),
        pytest.param("produtor", _declarar_fila, id="o producer não declara fila"),
        pytest.param("produtor", _apagar_a_fila, id="o producer não apaga a fila"),
        pytest.param("consumidor", _publicar_em(EXCHANGE), id="o consumer não publica"),
        pytest.param("consumidor", _ler_a_dlq, id="o consumer não lê a DLQ"),
        pytest.param("consumidor", _declarar_fila, id="o consumer não declara fila"),
        pytest.param("consumidor", _apagar_a_fila, id="o consumer não apaga a fila"),
    ],
)
def test_o_processo_nao_alcanca_o_que_nao_e_dele(canal, usuarios, quem, agir):
    with pytest.raises(ChannelClosedByBroker) as erro:
        agir(canal(getattr(usuarios, quem)))

    assert erro.value.reply_code == SEM_PERMISSAO


def test_o_publisher_de_verdade_funciona_so_com_a_permissao_do_producer(
    amqp_url, usuarios, novo_lote, bronze_queue
):
    publisher = RabbitTelemetryPublisher(
        _com_credencial(amqp_url, usuarios.produtor),
        timeout=5,
        heartbeat=60,
        connection_name="pytest:acl",
    )
    lote = novo_lote()

    publisher.publish(lote)

    [(propriedades, _)] = bronze_queue(minimo=1)
    assert propriedades.message_id == str(lote.message_id)


def _consumidor(amqp_url, usuario, handler) -> RabbitTelemetryConsumer:
    return RabbitTelemetryConsumer(
        _com_credencial(amqp_url, usuario),
        timeout=5,
        heartbeat=60,
        connection_name="pytest:acl",
        prefetch=1,
        handler=handler,
        observer=ObservadorEmMemoria(),
    )


def test_o_consumer_de_verdade_drena_a_fila_so_com_a_permissao_dele(
    amqp_url, usuarios, publicar, novo_lote, bronze_queue
):
    lote = novo_lote()
    publicar(encode(lote))
    gravados = []

    _consumidor(amqp_url, usuarios.consumidor, gravados.append).run(idle_timeout=1)

    assert gravados == [lote]
    assert bronze_queue() == []


def test_a_mensagem_recusada_chega_a_dlq_sem_o_consumer_poder_escrever(
    amqp_url, usuarios, publicar, dead_letters
):
    """Quem leva a mensagem morta é o broker: o consumer não precisa de escrita."""
    publicar(b"isto nao e json", message_id="fora-do-contrato")

    _consumidor(amqp_url, usuarios.consumidor, lambda _lote: None).run(idle_timeout=1)

    [(propriedades, _)] = dead_letters(minimo=1)
    assert propriedades.message_id == "fora-do-contrato"


def test_o_usuario_do_processo_nao_entra_na_api_de_gerenciamento(amqp_management_url, usuarios):
    api = ManagementApi(_com_credencial(amqp_management_url, usuarios.produtor), timeout=5)

    with pytest.raises(ManagementApiError, match="401"):
        api.ensure_vhost("teste-acl")


def test_aplicar_de_novo_troca_a_senha(amqp_url, amqp_management, amqp_topology, usuarios):
    """É assim que se troca a senha de um processo: muda no .env e o setup roda."""
    antiga = topology.BrokerUser("teste_acl_rotacao", _senha())
    nova = topology.BrokerUser(antiga.name, _senha())
    topology.apply_users(
        amqp_management, amqp_topology, producer=antiga, consumer=usuarios.consumidor
    )

    topology.apply_users(
        amqp_management, amqp_topology, producer=nova, consumer=usuarios.consumidor
    )

    _conectar(amqp_url, nova).close()
    with pytest.raises(AMQPConnectionError):
        _conectar(amqp_url, antiga)
