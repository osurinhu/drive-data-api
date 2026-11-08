"""O servidor de saúde do consumer, numa porta local e com as sondas de mentira."""

import json
import urllib.error
import urllib.request

import pytest

from instrumentation.consumer_health import ConsumerHealthServer


class Situacao:
    """O que o servidor pergunta: se consome, e quantas vezes pingou o Mongo."""

    def __init__(self) -> None:
        self.consumindo = True
        self.mongo = True
        self.pings = 0

    def bronze_ok(self) -> bool:
        self.pings += 1
        return self.mongo


@pytest.fixture
def situacao():
    return Situacao()


@pytest.fixture
def servidor(situacao):
    def _novo(bronze_ttl: float = 30.0) -> ConsumerHealthServer:
        server = ConsumerHealthServer(
            0,
            consuming=lambda: situacao.consumindo,
            bronze_ok=situacao.bronze_ok,
            bronze_ttl=bronze_ttl,
            host="127.0.0.1",
        )
        server.start()
        criados.append(server)
        return server

    criados: list[ConsumerHealthServer] = []
    yield _novo
    for server in criados:
        server.stop()


def _get(server: ConsumerHealthServer, caminho: str) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{server.port}{caminho}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resposta:
            return resposta.status, json.load(resposta)
    except urllib.error.HTTPError as erro:
        return erro.code, json.load(erro)


def test_consumindo_e_com_o_mongo_de_pe_esta_pronto(servidor):
    assert _get(servidor(), "/health/ready") == (
        200,
        {"status": "ok", "consuming": "yes", "mongo": "ok"},
    )


def test_sem_consumir_nao_esta_pronto_mas_esta_vivo(servidor, situacao):
    situacao.consumindo = False
    server = servidor()

    assert _get(server, "/health/ready") == (
        503,
        {"status": "unavailable", "consuming": "no", "mongo": "ok"},
    )
    assert _get(server, "/health/live") == (200, {"status": "ok"})


def test_sem_o_mongo_nao_esta_pronto_mesmo_com_a_fila_vazia(servidor, situacao):
    situacao.mongo = False

    assert _get(servidor(), "/health/ready") == (
        503,
        {"status": "unavailable", "consuming": "yes", "mongo": "unreachable"},
    )


def test_o_ping_que_deu_certo_vale_pelo_prazo(servidor, situacao):
    server = servidor()

    _get(server, "/health/ready")
    _get(server, "/health/ready")

    assert situacao.pings == 1


def test_passado_o_prazo_o_ping_se_repete(servidor, situacao):
    server = servidor(bronze_ttl=0)

    _get(server, "/health/ready")
    _get(server, "/health/ready")

    assert situacao.pings == 2


def test_a_volta_do_mongo_aparece_na_pergunta_seguinte(servidor, situacao):
    """O ping que falhou não fica guardado pelo prazo."""
    situacao.mongo = False
    server = servidor()
    _get(server, "/health/ready")

    situacao.mongo = True

    assert _get(server, "/health/ready")[0] == 200


def test_caminho_desconhecido_e_404(servidor):
    assert _get(servidor(), "/metrics")[0] == 404
