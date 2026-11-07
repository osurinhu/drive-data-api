"""Sondas de disponibilidade.

A raiz usa só o Postgres: as sondas do Mongo e do RabbitMQ são trocadas por
dublês que respondem "ok", e cada teste derruba a que precisar. O health com as
três dependências de verdade está em ``tests/e2e/``.
"""

import pytest

HEALTH_URL = "/api/v1/health/"
LIVE_URL = "/api/v1/health/live"


@pytest.fixture(autouse=True)
def _sondas_externas_respondem(app, monkeypatch):
    """Só a sonda do banco é de verdade."""
    for nome in app.extensions["health_probes"]:
        if nome != "database":
            monkeypatch.setitem(app.extensions["health_probes"], nome, lambda: True)


def _derrubar(app, monkeypatch, *dependencias):
    """Troca a sonda das dependências por uma que sempre falha."""
    for nome in dependencias:
        monkeypatch.setitem(app.extensions["health_probes"], nome, lambda: False)


def test_health_responde_sem_autenticacao_com_uma_chave_por_dependencia(client):
    """Precisa ser público: o healthcheck do container não tem token.

    O perfil da suíte ([default]) carrega todas as dependências, então todas
    aparecem, uma chave por dependência.
    """
    response = client.get(HEALTH_URL)

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "database": "ok",
        "mongo": "ok",
        "rabbitmq": "ok",
    }


def test_dependencia_nao_critica_fora_responde_200_degradado(app, client, monkeypatch):
    """Fora do HEALTH_CRITICAL, a queda aparece no corpo, mas o processo segue
    no balanceador: no api, login e cadastro funcionam sem o broker."""
    _derrubar(app, monkeypatch, "rabbitmq")

    response = client.get(HEALTH_URL)

    assert response.status_code == 200
    corpo = response.get_json()
    assert corpo["status"] == "degraded"
    assert corpo["rabbitmq"] == "unreachable"
    assert corpo["database"] == "ok"


def test_dependencia_critica_fora_responde_503(app, client, monkeypatch):
    _derrubar(app, monkeypatch, "database")

    response = client.get(HEALTH_URL)

    assert response.status_code == 503
    assert response.get_json()["database"] == "unreachable"


@pytest.mark.parametrize(
    ("criticas", "status"),
    [(["database"], 200), (["database", "mongo"], 503)],
)
def test_quem_decide_o_que_e_critico_e_o_health_critical(
    app, client, monkeypatch, criticas, status
):
    """O mesmo Mongo fora: 200 no perfil que não o lista (o api) e 503 no que lista
    (o bronze_api)."""
    _derrubar(app, monkeypatch, "mongo")
    monkeypatch.setitem(app.config, "HEALTH_CRITICAL", criticas)

    response = client.get(HEALTH_URL)

    assert response.status_code == status


def test_live_responde_sem_consultar_dependencia_nenhuma(app, client, monkeypatch):
    """O liveness reinicia o processo quando falha. Uma dependência fora não volta
    reiniciando quem depende dela, então a sonda de vida não as consulta."""

    def explode():
        raise AssertionError("o /live não deveria consultar sonda nenhuma")

    for nome in list(app.extensions["health_probes"]):
        monkeypatch.setitem(app.extensions["health_probes"], nome, explode)

    response = client.get(LIVE_URL)

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


# O id de requisição é conferido no /health/live: público e sem sonda nenhuma.


def test_a_resposta_devolve_o_request_id_no_header(client):
    response = client.get(LIVE_URL)

    assert response.headers["X-Request-ID"]


def test_a_resposta_honra_o_request_id_do_cliente(client):
    response = client.get(LIVE_URL, headers={"X-Request-ID": "rastreio-123"})

    assert response.headers["X-Request-ID"] == "rastreio-123"


PERMITIDOS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def test_request_id_do_cliente_e_sanitizado(client):
    """Id do cliente é entrada hostil e vai direto para as linhas de log.

    (Quebra de linha não entra neste teste porque o próprio Werkzeug a rejeita
    antes de chegar à aplicação — o protocolo HTTP não permite. O que de fato
    passa são espaços, pontuação e caracteres de controle.)
    """
    response = client.get(LIVE_URL, headers={"X-Request-ID": "id ruim; rm -rf / \t<script>"})

    devolvido = response.headers["X-Request-ID"]
    assert set(devolvido) <= PERMITIDOS


def test_request_id_longo_e_truncado(client):
    response = client.get(LIVE_URL, headers={"X-Request-ID": "a" * 500})

    assert len(response.headers["X-Request-ID"]) == 64
