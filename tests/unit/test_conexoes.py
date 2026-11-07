"""Conexões com o Mongo e o RabbitMQ: validação na subida, parâmetros e sondas.

Nada aqui precisa dos serviços. Onde a sonda precisa falhar, ela aponta para uma
porta local fechada (127.0.0.1:1), e a recusa é imediata.
"""

import time
import traceback

import pytest
from flask import Flask

from ext import mongo as ext_mongo
from ext import rabbitmq as ext_rabbitmq
from instrumentation.probes import register_probe, registered_probes
from messaging.connection import connection_parameters

SENHA = "senha-que-nao-pode-vazar"
MONGO_FECHADO = f"mongodb://ninguem:{SENHA}@127.0.0.1:1/?authSource=admin"
AMQP_FECHADO = f"amqp://ninguem:{SENHA}@127.0.0.1:1/teste"


def _app(**config) -> Flask:
    app = Flask("teste-conexoes")
    app.config.update(
        PROCESS="teste",
        MONGO_DATABASE="drive_data_bronze_test",
        MONGO_TIMEOUT_SECONDS=5,
        AMQP_HEARTBEAT_SECONDS=60,
        HEALTH_PROBE_TIMEOUT_SECONDS=0.5,
    )
    app.config.update(config)
    return app


# --- Registro das sondas ---------------------------------------------------------


def test_a_mesma_sonda_nao_pode_ser_registrada_duas_vezes():
    """Duas extensões com o mesmo nome de sonda: a segunda esconderia a primeira."""
    app = Flask("teste")
    register_probe(app, "mongo", lambda: True)

    with pytest.raises(RuntimeError, match="duas vezes"):
        register_probe(app, "mongo", lambda: True)


# --- Mongo --------------------------------------------------------------------------


@pytest.mark.parametrize("uri", ["", "postgresql://x:y@db/z", "localhost:27017"])
def test_mongo_sem_uri_valida_reprova_a_subida(uri):
    with pytest.raises(RuntimeError, match="MONGO_URI"):
        ext_mongo.init_app(_app(MONGO_URI=uri))


#: Sem codificação, o "/" corta a senha: o começo vira porta e o fim vira caminho,
#: e é por esses dois papéis que as mensagens do pika e do PyMongo a repetiriam.
SENHA_COM_BARRA = "comecoKq/fimZw"


@pytest.mark.parametrize(
    ("extensao", "chave", "url"),
    [
        (ext_mongo, "MONGO_URI", f"http://ninguem:{SENHA}@x"),
        (ext_mongo, "MONGO_URI", f"mongodb://ninguem:{SENHA_COM_BARRA}@mongo:27017/"),
        (ext_rabbitmq, "AMQP_URL", f"http://ninguem:{SENHA}@x"),
        (ext_rabbitmq, "AMQP_URL", f"amqp://ninguem:{SENHA_COM_BARRA}@rabbitmq:5672/v"),
    ],
)
def test_a_configuracao_invalida_nao_repete_a_senha_nem_em_pedacos(extensao, chave, url):
    """Nem na mensagem nem no traceback, que vai inteiro para o log de quem sobe."""
    with pytest.raises(RuntimeError) as erro:
        extensao.init_app(_app(**{chave: url}))

    traceback_inteiro = "".join(traceback.format_exception(erro.value))
    assert chave in str(erro.value)
    for trecho in (SENHA, *SENHA_COM_BARRA.split("/")):
        assert trecho not in traceback_inteiro, trecho


class _ClienteFalso:
    def __init__(self, uri, **opcoes):
        self.uri = uri
        self.opcoes = opcoes


@pytest.fixture
def clientes_criados(monkeypatch):
    """Troca o ``MongoClient`` por um falso e devolve a lista dos que foram criados."""
    criados = []

    def _criar(uri, **opcoes):
        criados.append(_ClienteFalso(uri, **opcoes))
        return criados[-1]

    monkeypatch.setattr(ext_mongo, "MongoClient", _criar)
    return criados


def test_o_cliente_do_mongo_nasce_na_subida_sem_conectar_e_e_reaproveitado(clientes_criados):
    """Na subida, para o PyMongo validar a URI; com ``connect=False``, para não abrir
    conexão, thread nem consulta de DNS antes do primeiro uso."""
    app = _app(MONGO_URI=MONGO_FECHADO)

    ext_mongo.init_app(app)
    conexao = app.extensions["mongo"]
    primeiro, segundo = conexao.client, conexao.client

    assert primeiro is segundo
    assert len(clientes_criados) == 1
    assert clientes_criados[0].opcoes["connect"] is False


def test_depois_de_um_fork_o_processo_filho_cria_o_proprio_cliente(clientes_criados, monkeypatch):
    """O cliente herdado do pai não é usado: o pool e as threads dele são do pai."""
    app = _app(MONGO_URI=MONGO_FECHADO)
    ext_mongo.init_app(app)
    conexao = app.extensions["mongo"]
    do_pai = conexao.client

    monkeypatch.setattr(ext_mongo.os, "getpid", lambda: -1)
    do_filho = conexao.client

    assert do_filho is not do_pai
    assert len(clientes_criados) == 2


@pytest.mark.usefixtures("clientes_criados")
def test_o_cliente_do_mongo_usa_os_prazos_e_devolve_datas_com_fuso():
    app = _app(MONGO_URI=MONGO_FECHADO, MONGO_TIMEOUT_SECONDS=3)

    ext_mongo.init_app(app)
    cliente = app.extensions["mongo"].client

    assert cliente.uri == MONGO_FECHADO
    assert cliente.opcoes["serverSelectionTimeoutMS"] == 3000
    assert cliente.opcoes["connectTimeoutMS"] == 3000
    assert cliente.opcoes["tz_aware"] is True
    assert cliente.opcoes["appname"] == "drive-data:teste"


@pytest.fixture
def sonda_do_mongo_fechado():
    """A sonda de um Mongo numa porta fechada; o cliente é fechado no fim."""
    app = _app(MONGO_URI=MONGO_FECHADO)
    ext_mongo.init_app(app)
    yield registered_probes(app)["mongo"]
    app.extensions["mongo"].client.close()


def test_a_sonda_do_mongo_responde_false_dentro_do_prazo_dela(sonda_do_mongo_fechado):
    """O prazo da sonda (0,5 s aqui) vale sobre o do cliente (5 s): uma dependência
    fora não pode estourar o timeout do healthcheck do compose."""
    inicio = time.monotonic()
    respondeu = sonda_do_mongo_fechado()
    duracao = time.monotonic() - inicio

    assert respondeu is False
    assert duracao < 3, duracao


# --- RabbitMQ ---------------------------------------------------------------------


@pytest.mark.parametrize("url", ["", "http://x:y@rabbitmq", "redis://rabbitmq:6379"])
def test_rabbitmq_sem_url_valida_reprova_a_subida(url):
    with pytest.raises(RuntimeError, match="AMQP_URL"):
        ext_rabbitmq.init_app(_app(AMQP_URL=url))


def test_os_parametros_amqp_seguem_os_prazos_do_projeto():
    """Os prazos do settings.toml valem sobre os da URL, e a conexão se identifica
    no painel do RabbitMQ."""
    parametros = connection_parameters(
        "amqp://u:s@rabbitmq:5672/drive-data?heartbeat=600&socket_timeout=99",
        timeout=4,
        heartbeat=30,
        connection_name="drive-data:api:publisher",
    )

    assert parametros.host == "rabbitmq"
    assert parametros.virtual_host == "drive-data"
    assert parametros.socket_timeout == 4
    assert parametros.blocked_connection_timeout == 4
    assert parametros.heartbeat == 30
    assert parametros.connection_attempts == 1
    assert parametros.client_properties == {"connection_name": "drive-data:api:publisher"}


def test_a_sonda_do_rabbitmq_responde_false_com_o_broker_fora(caplog):
    """O log diz por que falhou (o erro de conexão do pika tem str() vazio) e não
    repete a senha da URL."""
    app = _app(AMQP_URL=AMQP_FECHADO)
    ext_rabbitmq.init_app(app)

    respondeu = registered_probes(app)["rabbitmq"]()

    assert respondeu is False
    assert "Connection refused" in caplog.text
    assert SENHA not in caplog.text
