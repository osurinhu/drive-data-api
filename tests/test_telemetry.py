"""POST /telemetry/batches: formato, posse por registro, permissão e o 503.

O publisher é trocado pelo ``FilaEmMemoria``: aqui se testa a rota sobre o
Postgres. A publicação de verdade está em ``tests/rabbitmq/`` e o caminho
inteiro, em ``tests/e2e/``.
"""

from uuid import UUID, uuid4

import pytest
from dependency_injector import providers

from domain.authz import Role
from messaging.publisher import RabbitTelemetryPublisher

URL = "/api/v1/telemetry/batches"
MEU = "93YBSR7RHEJ298116"
ALHEIO = "9BWZZZ377VT004251"


@pytest.fixture
def fila(app, fila_em_memoria):
    """A aplicação publicando no ``FilaEmMemoria``."""
    app.container.telemetry_publisher.override(providers.Object(fila_em_memoria))
    yield fila_em_memoria
    app.container.telemetry_publisher.reset_override()


def _registro(vin: str = MEU, **extra) -> dict:
    return {
        "id": str(uuid4()),
        "vin": vin,
        "collected_at": "2026-11-07T12:00:00.482Z",
        "readings": [
            {"metric": "rpm", "unit": "rpm", "available": True, "value": 1065.0},
            {"metric": "speed", "unit": "km/h", "available": False, "value": None},
        ],
        **extra,
    }


def _cadastrar(client, headers, vin: str = MEU) -> int:
    response = client.post("/api/v1/vehicle/", json={"vin": vin}, headers=headers)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def test_lote_misto_responde_202_e_publica_so_o_autorizado(client, login, fila):
    usuario, headers = login("joao")
    veiculo = _cadastrar(client, headers)
    meu, alheio = _registro(user="joao", campo_do_app="x"), _registro(ALHEIO)

    response = client.post(
        URL, json={"records": [meu, alheio]}, headers={**headers, "X-Request-ID": "req-lote-1"}
    )

    assert response.status_code == 202, response.get_json()
    recibo = response.get_json()
    [lote] = fila.publicados
    assert recibo["batch_id"] == str(lote.message_id)
    assert recibo["accepted"] == 1
    assert recibo["rejected"] == [
        {"index": 1, "id": alheio["id"], "vin": ALHEIO, "reason": "vehicle_not_authorized"}
    ]
    assert lote.sender.user_id == usuario["id"]
    assert lote.request_id == "req-lote-1"
    [registro] = lote.records
    assert registro.record_id == UUID(meu["id"])
    assert registro.vehicle_id == veiculo
    assert registro.sender_relation == "owner"
    assert registro.payload_user_matches_sender is True
    assert registro.raw == meu  # inteiro, com o campo que o schema não conhece


def test_vin_em_minusculas_e_aceito_e_o_bruto_fica_como_veio(client, login, fila):
    _, headers = login()
    _cadastrar(client, headers)
    registro = _registro(MEU.lower())

    response = client.post(URL, json={"records": [registro]}, headers=headers)

    assert response.status_code == 202
    [publicado] = fila.publicados[0].records
    assert publicado.vin == MEU
    assert publicado.raw["vin"] == MEU.lower()


def test_nada_autorizado_responde_202_sem_batch_id_e_nao_publica(client, headers, fila):
    response = client.post(URL, json={"records": [_registro(ALHEIO)]}, headers=headers)

    assert response.status_code == 202
    assert response.get_json()["batch_id"] is None
    assert response.get_json()["accepted"] == 0
    assert fila.publicados == []


def test_veiculo_de_outro_gestor_e_rejeitado(client, login, fila):
    _, do_dono = login("dono")
    _cadastrar(client, do_dono)
    _, de_outro = login("outro")

    response = client.post(URL, json={"records": [_registro()]}, headers=de_outro)

    assert response.get_json()["rejected"][0]["reason"] == "vehicle_not_authorized"
    assert fila.publicados == []


def test_a_posse_vale_no_envio_veiculo_excluido_e_rejeitado(client, headers, fila):
    veiculo = _cadastrar(client, headers)
    client.delete(f"/api/v1/vehicle/{veiculo}", headers=headers)

    response = client.post(URL, json={"records": [_registro()]}, headers=headers)

    assert response.get_json()["accepted"] == 0
    assert fila.publicados == []


def test_o_campo_user_do_payload_nao_autoriza_nada(client, login, fila):
    """O dono vem do token. Dizer no payload que é o dono do carro não muda nada."""
    _, do_dono = login("dono")
    _cadastrar(client, do_dono)
    _, de_outro = login("outro")

    response = client.post(URL, json={"records": [_registro(user="dono")]}, headers=de_outro)

    assert response.get_json()["accepted"] == 0
    assert fila.publicados == []


def test_fila_fora_responde_503_com_retry_after(client, headers, fila):
    _cadastrar(client, headers)
    fila.falhar = True

    response = client.post(URL, json={"records": [_registro()]}, headers=headers)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert "Tente novamente" in response.get_json()["message"]


def test_broker_parado_responde_503_com_retry_after(app, client, headers):
    """O adaptador de verdade, apontado para uma porta fechada: é o broker parado,
    sem dublê. A conexão recusada vira 503, e o app sabe quando reenviar."""
    _cadastrar(client, headers)
    parado = RabbitTelemetryPublisher(
        "amqp://ninguem:nada@127.0.0.1:1/teste",
        timeout=2,
        heartbeat=60,
        connection_name="pytest:publisher",
    )
    app.container.telemetry_publisher.override(providers.Object(parado))
    try:
        response = client.post(URL, json={"records": [_registro()]}, headers=headers)
    finally:
        app.container.telemetry_publisher.reset_override()

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.get_json()["request_id"]


@pytest.mark.parametrize("papel", [Role.SILVER, Role.LEITOR])
def test_papel_sem_telemetria_enviar_recebe_403(client, login, fila, papel):
    _, headers = login("etl", role=papel)

    response = client.post(URL, json={"records": [_registro()]}, headers=headers)

    assert response.status_code == 403
    assert fila.publicados == []


def test_o_403_vem_antes_da_validacao_do_corpo(client, login, fila):
    """Quem não pode enviar não descobre o formato do lote num 422."""
    _, headers = login("etl", role=Role.SILVER)

    response = client.post(URL, json={"campo": "x"}, headers=headers)

    assert response.status_code == 403
    assert fila.publicados == []


def test_sem_token_responde_401(client):
    assert client.post(URL, json={"records": [_registro()]}).status_code == 401


def _com_leitura(**campos) -> dict:
    registro = _registro()
    registro["readings"] = [{**registro["readings"][0], **campos}]
    return registro


@pytest.mark.parametrize(
    "corpo",
    [
        pytest.param({}, id="sem records"),
        pytest.param({"records": []}, id="lote vazio"),
        pytest.param({"records": [_registro()] * 501}, id="mais de 500 registros"),
        pytest.param({"records": [_registro()], "extra": 1}, id="campo desconhecido na raiz"),
        pytest.param({"records": [_registro("93YBSR7RHEJ29811O")]}, id="VIN com O"),
        pytest.param({"records": [{**_registro(), "id": "nao-e-uuid"}]}, id="id sem UUID"),
        pytest.param(
            {"records": [{**_registro(), "collected_at": "2026-11-07T12:00:00"}]},
            id="data sem fuso",
        ),
        pytest.param({"records": [{**_registro(), "readings": []}]}, id="sem leituras"),
        pytest.param({"records": [_com_leitura(metric="RPM")]}, id="métrica fora do padrão"),
        pytest.param({"records": [_com_leitura(unit="u" * 17)]}, id="unidade longa"),
    ],
)
def test_formato_invalido_reprova_o_lote_inteiro(client, headers, fila, corpo):
    response = client.post(URL, json=corpo, headers=headers)

    assert response.status_code == 422, response.get_json()
    assert fila.publicados == []
