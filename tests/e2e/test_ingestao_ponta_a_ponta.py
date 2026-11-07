"""O envio de lote do HTTP à fila, com o publisher de verdade, e o comando de setup.

Nada de dublê: a rota, o serviço, o Postgres (a posse) e o RabbitMQ do vhost de
testes, como o container os monta.
"""

from uuid import uuid4

import pytest

from domain.contracts.telemetry_batch import decode

URL = "/api/v1/telemetry/batches"
MEU = "93YBSR7RHEJ298116"


def _registro(vin: str) -> dict:
    return {
        "id": str(uuid4()),
        "vin": vin,
        "collected_at": "2026-11-07T12:00:00Z",
        "readings": [{"metric": "rpm", "unit": "rpm", "available": True, "value": 800.0}],
    }


def test_o_lote_aceito_chega_na_fila_e_o_rejeitado_nao(client, headers, bronze_queue):
    assert client.post("/api/v1/vehicle/", json={"vin": MEU}, headers=headers).status_code == 201
    aceito, rejeitado = _registro(MEU), _registro("9BWZZZ377VT004251")

    response = client.post(
        URL, json={"records": [aceito, rejeitado]}, headers={**headers, "X-Request-ID": "e2e-1"}
    )

    assert response.status_code == 202, response.get_json()
    [(propriedades, corpo)] = bronze_queue()
    lote = decode(corpo)
    assert str(lote.message_id) == response.get_json()["batch_id"]
    assert propriedades.correlation_id == "e2e-1"
    assert [str(r.record_id) for r in lote.records] == [aceito["id"]]


@pytest.fixture
def cli(app):
    return app.test_cli_runner()


def test_o_comando_de_setup_aplica_a_topologia(app, cli, amqp_management_url, monkeypatch):
    monkeypatch.setitem(app.config, "AMQP_MANAGEMENT_URL", amqp_management_url)

    resultado = cli.invoke(args=["rabbitmq", "setup"])

    assert resultado.exit_code == 0, resultado.output
    assert "drive-data-test" in resultado.output


def test_o_comando_de_setup_exige_a_url_de_gerenciamento(app, cli, monkeypatch):
    monkeypatch.setitem(app.config, "AMQP_MANAGEMENT_URL", "")

    resultado = cli.invoke(args=["rabbitmq", "setup"])

    assert resultado.exit_code != 0
    assert "AMQP_MANAGEMENT_URL não definida" in resultado.output
