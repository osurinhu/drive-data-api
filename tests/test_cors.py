"""CORS fechado: nenhum site alheio chama a API pelo navegador de quem está logado."""

ORIGEM = "https://outro-site.example"


def test_a_resposta_nao_libera_outra_origem(client):
    resposta = client.get("/api/v1/health/live", headers={"Origin": ORIGEM})

    assert resposta.status_code == 200
    assert "Access-Control-Allow-Origin" not in resposta.headers


def test_o_preflight_nao_libera_outra_origem(client):
    resposta = client.options(
        "/api/v1/vehicle/",
        headers={"Origin": ORIGEM, "Access-Control-Request-Method": "POST"},
    )

    assert "Access-Control-Allow-Origin" not in resposta.headers
