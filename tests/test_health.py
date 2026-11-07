"""Sonda de disponibilidade."""

HEALTH_URL = "/api/v1/health/"


def test_health_responde_sem_autenticacao(client):
    """Precisa ser público: o healthcheck do container não tem token."""
    response = client.get(HEALTH_URL)

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": "ok"}


def test_health_devolve_o_request_id_no_header(client):
    response = client.get(HEALTH_URL)

    assert response.headers["X-Request-ID"]


def test_health_honra_o_request_id_do_cliente(client):
    response = client.get(HEALTH_URL, headers={"X-Request-ID": "rastreio-123"})

    assert response.headers["X-Request-ID"] == "rastreio-123"


PERMITIDOS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def test_request_id_do_cliente_e_sanitizado(client):
    """Id do cliente é entrada hostil e vai direto para as linhas de log.

    (Quebra de linha não entra neste teste porque o próprio Werkzeug a rejeita
    antes de chegar à aplicação — o protocolo HTTP não permite. O que de fato
    passa são espaços, pontuação e caracteres de controle.)
    """
    response = client.get(HEALTH_URL, headers={"X-Request-ID": "id ruim; rm -rf / \t<script>"})

    devolvido = response.headers["X-Request-ID"]
    assert set(devolvido) <= PERMITIDOS


def test_request_id_longo_e_truncado(client):
    response = client.get(HEALTH_URL, headers={"X-Request-ID": "a" * 500})

    assert len(response.headers["X-Request-ID"]) == 64
