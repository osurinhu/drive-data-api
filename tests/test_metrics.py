"""O ``/metrics`` e o log de acesso da aplicação, pela porta HTTP."""

import logging

from prometheus_client import REGISTRY

from instrumentation.correlation import current_correlation

HEALTH_LIVE = "/api/v1/health/live"


def _contagem(route: str, status: str) -> float:
    valor = REGISTRY.get_sample_value("http_requests_total", {"route": route, "status": status})
    return valor or 0.0


def test_metrics_responde_sem_token_no_formato_do_prometheus(client):
    resposta = client.get("/metrics")

    assert resposta.status_code == 200
    assert resposta.content_type.startswith("text/plain")
    assert b"http_requests_total" in resposta.data


def test_metrics_nao_entra_no_openapi(client):
    spec = client.get("/api/v1/openapi.json").get_json()

    assert not any("metrics" in caminho for caminho in spec["paths"])


def test_a_coleta_nao_se_conta_nem_vai_para_o_log(client, caplog):
    antes = _contagem("/metrics", "200")

    with caplog.at_level(logging.INFO, logger="http.access"):
        client.get("/metrics")

    assert _contagem("/metrics", "200") == antes
    assert [r for r in caplog.records if r.name == "http.access"] == []


def test_o_rotulo_e_o_molde_da_rota_e_nao_a_url(client, headers):
    """Com a URL, cada id viraria uma série nova no Prometheus."""
    molde = "/api/v1/vehicle/<int:vehicle_id>"
    antes = _contagem(molde, "404")

    client.get("/api/v1/vehicle/987654", headers=headers)

    assert _contagem(molde, "404") == antes + 1
    assert _contagem("/api/v1/vehicle/987654", "404") == 0


def test_url_inventada_cai_num_rotulo_so(client):
    antes = _contagem("unmatched", "404")

    client.get("/qualquer/coisa/inventada")

    assert _contagem("unmatched", "404") == antes + 1


def test_o_log_de_acesso_traz_rota_status_e_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="http.access"):
        client.get(HEALTH_LIVE, headers={"X-Request-ID": "id-do-teste"})

    [linha] = [r for r in caplog.records if r.name == "http.access"]
    assert linha.event == "http.access"
    assert (linha.method, linha.path, linha.route) == ("GET", HEALTH_LIVE, HEALTH_LIVE)
    assert linha.status == 200
    assert linha.duration_ms >= 0


def test_o_usuario_do_token_entra_na_correlacao_do_log_de_acesso(client, login, caplog):
    """O ``user_id`` nasce na autenticação, e o log de acesso sai depois dela."""
    usuario, cabecalhos = login("quem-acessou")
    capturados = []

    class Captura(logging.Handler):
        def emit(self, _record):
            capturados.append(current_correlation())

    handler = Captura()
    logger = logging.getLogger("http.access")
    logger.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO, logger="http.access"):
            client.get("/api/v1/auth/me", headers=cabecalhos)
    finally:
        logger.removeHandler(handler)

    assert capturados[-1]["user_id"] == usuario["id"]
