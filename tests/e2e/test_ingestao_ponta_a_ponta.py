"""O lote do HTTP ao bronze, com os adaptadores de verdade, e os comandos de setup.

Nada de dublê: a rota, o serviço, o Postgres (a posse), o RabbitMQ do vhost de
testes e o Mongo de testes, como os dois containers os montam.
"""

from uuid import uuid4

import pytest

from domain.contracts.telemetry_batch import decode
from repository.mongo import indexes
from repository.mongo.bronze_document import RECORDS

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


def _consumir(app) -> None:
    """O consumer de verdade, do container do consumer, drena a fila e para."""
    with app.app_context():
        app.consumer_container.telemetry_consumer().run(idle_timeout=1)


def test_o_lote_aceito_vira_documento_no_bronze_e_o_reenvio_nao_duplica(
    app, client, login, bronze_queue, mongo_db
):
    usuario, headers = login("gestora")
    assert client.post("/api/v1/vehicle/", json={"vin": MEU}, headers=headers).status_code == 201
    registro = _registro(MEU)

    resposta = client.post(
        URL, json={"records": [registro]}, headers={**headers, "X-Request-ID": "e2e-2"}
    )
    _consumir(app)

    assert resposta.status_code == 202, resposta.get_json()
    [documento] = mongo_db[RECORDS].find()
    assert documento["_id"] == registro["id"]
    assert documento["raw"] == registro
    assert documento["ingestion"]["user_id"] == usuario["id"]
    assert documento["ingestion"]["sender_relation"] == "owner"
    assert documento["ingestion"]["request_id"] == "e2e-2"
    assert documento["ingestion"]["message_id"] == resposta.get_json()["batch_id"]
    assert documento["ingestion"]["available_metrics"] == ["rpm"]

    # O app reenvia o mesmo lote (não recebeu o 202, por exemplo): outro envelope,
    # os mesmos registros.
    reenvio = client.post(URL, json={"records": [registro]}, headers=headers)
    _consumir(app)

    assert reenvio.status_code == 202
    assert mongo_db[RECORDS].count_documents({}) == 1
    assert bronze_queue() == []


@pytest.fixture
def cli(app):
    return app.test_cli_runner()


def test_o_comando_mongo_setup_cria_os_indices_do_bronze(cli, mongo_db):
    resultado = cli.invoke(args=["mongo", "setup"])

    assert resultado.exit_code == 0, resultado.output
    assert indexes.INGESTED in resultado.output
    assert indexes.INGESTED in mongo_db[RECORDS].index_information()


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
