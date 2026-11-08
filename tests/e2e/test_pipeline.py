"""O pipeline inteiro, no processo: app → producer → fila → consumer → bronze → silver.

Nada de dublê: a rota de envio, a posse no Postgres, o RabbitMQ do vhost de testes,
o consumer de verdade, o Mongo de testes e a rota de leitura da silver. Só a janela
de estabilização vai a zero: o consumer termina de gravar antes de a silver ler, e
esperar 30 s não provaria nada a mais.
"""

from uuid import uuid4

import pytest
from dependency_injector import providers

from domain.authz import Role
from repository.mongo.bronze_document import REJECTIONS
from service.telemetry.bronze_read_service import BronzeReadService

ENVIO = "/api/v1/telemetry/batches"
LEITURA = "/api/v1/bronze/records"
MEU = "93YBSR7RHEJ298116"
ALHEIO = "9BWZZZ377VT004251"


@pytest.fixture
def sem_janela(app):
    app.container.bronze_read_service.override(
        providers.Factory(BronzeReadService, reader=app.container.bronze_reader, settle_seconds=0)
    )
    yield
    app.container.bronze_read_service.reset_override()


def _registro(vin: str) -> dict:
    return {
        "id": str(uuid4()),
        "vin": vin,
        "collected_at": "2026-11-07T12:00:00.482Z",
        "user": "gestora",
        "readings": [{"metric": "rpm", "unit": "rpm", "available": True, "value": 800.0}],
    }


def _consumir(app) -> None:
    """O consumer de verdade, do container do consumer, drena a fila e para."""
    with app.app_context():
        app.consumer_container.telemetry_consumer().run(idle_timeout=1)


def _ler_tudo(client, headers, cursor=None, *, limit: int) -> tuple[list[dict], str, int]:
    """Lê como a silver: página a página, até ``has_more`` ser falso. Devolve os
    itens, o último cursor e o número de chamadas."""
    itens, chamadas, mais = [], 0, True
    while mais:
        parametros = {"limit": limit} | ({"cursor": cursor} if cursor else {})
        response = client.get(LEITURA, query_string=parametros, headers=headers)
        assert response.status_code == 200, response.get_json()
        pagina = response.get_json()
        itens += pagina["items"]
        cursor, mais = pagina["next_cursor"], pagina["has_more"]
        chamadas += 1
    return itens, cursor, chamadas


@pytest.mark.usefixtures("sem_janela")
def test_a_silver_le_cada_registro_aceito_uma_vez_so(app, client, login, bronze_queue):
    gestora, do_app = login("gestora")
    _, da_silver = login("silver-etl", role=Role.SILVER)
    assert client.post("/api/v1/vehicle/", json={"vin": MEU}, headers=do_app).status_code == 201
    aceitos = [_registro(MEU) for _ in range(3)]
    lote = {"records": [*aceitos, _registro(ALHEIO)]}

    envio = client.post(ENVIO, json=lote, headers={**do_app, "X-Request-ID": "e2e-pipeline"})
    _consumir(app)
    itens, cursor, chamadas = _ler_tudo(client, da_silver, limit=2)

    assert envio.status_code == 202, envio.get_json()
    assert envio.get_json()["accepted"] == 3
    assert chamadas == 2  # 2 + 1: o `has_more` da primeira página levou à segunda
    assert sorted(item["record_id"] for item in itens) == sorted(r["id"] for r in aceitos)
    for item in itens:
        assert item["user_id"] == gestora["id"]
        assert item["sender_relation"] == "owner"
        assert item["request_id"] == "e2e-pipeline"
        assert item["message_id"] == envio.get_json()["batch_id"]
        assert item["payload_user_matches_sender"] is True
    assert {item["record_id"]: item["raw"] for item in itens} == {r["id"]: r for r in aceitos}

    # O app reenvia o mesmo lote (não recebeu o 202, por exemplo): outro envelope, os
    # mesmos registros. A silver continua de onde parou e não recebe nada de novo.
    reenvio = client.post(ENVIO, json=lote, headers=do_app)
    _consumir(app)
    depois, cursor_depois, _ = _ler_tudo(client, da_silver, cursor, limit=2)

    assert reenvio.status_code == 202
    assert depois == []
    assert cursor_depois == cursor
    assert bronze_queue() == []
    # Relendo do começo, o bronze tem os mesmos três.
    assert len(_ler_tudo(client, da_silver, limit=1000)[0]) == 3


@pytest.mark.usefixtures("sem_janela")
def test_o_motorista_envia_e_a_silver_ve_o_vinculo(app, client, login, bronze_queue):
    """A gestora cadastra o veículo e adiciona o motorista; ele envia, e o registro
    chega à silver com o remetente e o vínculo dele. Removido, o envio seguinte volta
    como `vehicle_not_authorized` e não chega ao bronze."""
    _, da_gestora = login("gestora")
    motorista, do_motorista = login("joao")
    _, da_silver = login("silver-etl", role=Role.SILVER)
    veiculo = client.post("/api/v1/vehicle/", json={"vin": MEU}, headers=da_gestora)
    motoristas = f"/api/v1/vehicle/{veiculo.get_json()['id']}/drivers"
    adicao = client.post(motoristas, json={"user_name": "joao"}, headers=da_gestora)
    enviado = _registro(MEU)

    envio = client.post(ENVIO, json={"records": [enviado]}, headers=do_motorista)
    _consumir(app)
    [item], cursor, _ = _ler_tudo(client, da_silver, limit=10)

    assert (veiculo.status_code, adicao.status_code, envio.status_code) == (201, 201, 202)
    assert item["record_id"] == enviado["id"]
    assert item["user_id"] == motorista["id"]
    assert item["vehicle_id"] == veiculo.get_json()["id"]
    assert item["sender_relation"] == "driver"
    assert item["payload_user_matches_sender"] is False  # o app disse "gestora"

    client.delete(f"{motoristas}/{motorista['id']}", headers=da_gestora)
    recusado = client.post(ENVIO, json={"records": [_registro(MEU)]}, headers=do_motorista)
    _consumir(app)

    assert recusado.get_json()["accepted"] == 0
    assert recusado.get_json()["rejected"][0]["reason"] == "vehicle_not_authorized"
    assert _ler_tudo(client, da_silver, cursor, limit=10)[0] == []
    assert bronze_queue() == []


@pytest.mark.usefixtures("sem_janela")
def test_id_de_outro_remetente_nao_sobrescreve_e_deixa_rastro(
    app, client, login, bronze_queue, mongo_db
):
    """Duas pessoas, dois veículos, o mesmo id de registro: o primeiro fica no bronze, e o
    segundo vai para as recusas, com a mensagem confirmada."""
    gestora, da_gestora = login("gestora")
    outra, da_outra = login("outra")
    _, da_silver = login("silver-etl", role=Role.SILVER)
    client.post("/api/v1/vehicle/", json={"vin": MEU}, headers=da_gestora)
    client.post("/api/v1/vehicle/", json={"vin": ALHEIO}, headers=da_outra)
    original = _registro(MEU)
    colidente = {**_registro(ALHEIO), "id": original["id"]}

    primeiro = client.post(ENVIO, json={"records": [original]}, headers=da_gestora)
    _consumir(app)
    segundo = client.post(
        ENVIO, json={"records": [colidente]}, headers={**da_outra, "X-Request-ID": "e2e-colisao"}
    )
    _consumir(app)
    [item], _, _ = _ler_tudo(client, da_silver, limit=10)

    assert (primeiro.status_code, segundo.status_code) == (202, 202)
    assert (item["user_id"], item["raw"]) == (gestora["id"], original)
    [recusa] = mongo_db[REJECTIONS].find()
    assert recusa["reason"] == "id_collision"
    assert recusa["user_id"] == outra["id"]
    assert recusa["request_id"] == "e2e-colisao"
    assert recusa["raw"] == colidente
    assert bronze_queue() == []
