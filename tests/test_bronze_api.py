"""GET /bronze/records: a permissão, a query, o envelope por cursor e o 503.

O leitor do bronze é trocado pelo ``LeituraEmMemoria``: aqui se testa a rota, o
serviço e o schema sobre o Postgres (os usuários). O adaptador contra o Mongo de
verdade está em ``tests/mongo/`` e o caminho inteiro, em ``tests/e2e/``.
"""

import pytest
from dependency_injector import providers

from domain.authz import Role

URL = "/api/v1/bronze/records"


@pytest.fixture
def bronze(app, leitura_em_memoria):
    """A aplicação lendo o ``LeituraEmMemoria``."""
    app.container.bronze_reader.override(providers.Object(leitura_em_memoria))
    yield leitura_em_memoria
    app.container.bronze_reader.reset_override()


@pytest.fixture
def silver(login):
    _, headers = login("silver-etl", role=Role.SILVER)
    return headers


def test_a_silver_le_o_bronze(client, silver, bronze):
    registro = bronze.gravar(60)

    response = client.get(URL, headers=silver)

    assert response.status_code == 200, response.get_json()
    pagina = response.get_json()
    assert set(pagina) == {"items", "next_cursor", "has_more", "watermark"}
    assert pagina["has_more"] is False
    [item] = pagina["items"]
    assert item == {
        "record_id": str(registro.record_id),
        "vin": registro.vin,
        "vehicle_id": registro.vehicle_id,
        "user_id": registro.user_id,
        "sender_relation": "owner",
        "collected_at": registro.collected_at.isoformat(),
        "received_at": registro.received_at.isoformat(),
        "ingested_at": registro.ingested_at.isoformat(),
        "message_id": str(registro.message_id),
        "request_id": registro.request_id,
        "schema_version": 1,
        "payload_user_matches_sender": True,
        "raw": registro.raw,
    }


def test_o_que_foi_gravado_ha_pouco_fica_para_depois(client, silver, bronze):
    """A janela de estabilização (``BRONZE_SETTLE_SECONDS``) vem do settings.toml."""
    bronze.gravar(5)

    pagina = client.get(URL, headers=silver).get_json()

    assert pagina["items"] == []


def test_o_cursor_pagina_sem_perder_nem_repetir(client, silver, bronze):
    registros = [bronze.gravar(90), bronze.gravar(80), bronze.gravar(70)]

    primeira = client.get(URL, query_string={"limit": 2}, headers=silver).get_json()
    segunda = client.get(
        URL, query_string={"limit": 2, "cursor": primeira["next_cursor"]}, headers=silver
    ).get_json()
    vazia = client.get(
        URL, query_string={"limit": 2, "cursor": segunda["next_cursor"]}, headers=silver
    ).get_json()

    lidos = [item["record_id"] for item in primeira["items"] + segunda["items"]]
    assert lidos == [str(r.record_id) for r in registros]
    assert (primeira["has_more"], segunda["has_more"], vazia["has_more"]) == (True, False, False)
    assert vazia["items"] == []
    assert vazia["next_cursor"] == segunda["next_cursor"]


def test_a_silver_le_os_registros_de_todos_os_remetentes(client, silver, bronze):
    bronze.gravar(60, user_id=51)
    bronze.gravar(60, user_id=52)

    pagina = client.get(URL, headers=silver).get_json()

    assert {item["user_id"] for item in pagina["items"]} == {51, 52}


def test_os_filtros_da_query_recortam_a_leitura(client, silver, bronze):
    procurado = bronze.gravar(60, vin="9BWZZZ377VT004251", metricas=("rpm", "speed"))
    bronze.gravar(60, metricas=("speed",))
    bronze.gravar(60, vin="9BWZZZ377VT004251", metricas=("maf",))

    response = client.get(
        URL,
        query_string={"vin": "9bwzzz377vt004251", "metric": ["speed", "rpm"]},
        headers=silver,
    )

    assert response.status_code == 200, response.get_json()
    assert [i["record_id"] for i in response.get_json()["items"]] == [str(procurado.record_id)]


def test_o_cursor_continua_a_leitura_com_os_mesmos_filtros(client, silver, bronze):
    vin = "9BWZZZ377VT004251"
    registros = [bronze.gravar(90, vin=vin), bronze.gravar(80), bronze.gravar(70, vin=vin)]
    parametros = {"vin": vin, "limit": 1}

    primeira = client.get(URL, query_string=parametros, headers=silver).get_json()
    segunda = client.get(
        URL, query_string={**parametros, "cursor": primeira["next_cursor"]}, headers=silver
    ).get_json()

    lidos = [item["record_id"] for item in primeira["items"] + segunda["items"]]
    assert lidos == [str(registros[0].record_id), str(registros[2].record_id)]
    assert (primeira["has_more"], segunda["has_more"]) == (True, False)


def test_o_cursor_de_outro_filtro_responde_422(client, silver, bronze):
    bronze.gravar(60)
    cursor = client.get(URL, headers=silver).get_json()["next_cursor"]
    bronze.chamadas.clear()

    response = client.get(
        URL, query_string={"cursor": cursor, "vin": "9BWZZZ377VT004251"}, headers=silver
    )

    assert response.status_code == 422, response.get_json()
    assert response.get_json()["errors"]["query"] == {
        "cursor": ["O cursor pertence a outro filtro."]
    }
    assert bronze.chamadas == []


def test_ingested_from_comeca_a_leitura(client, silver, bronze):
    bronze.gravar(90)
    no_ponto = bronze.gravar(60)

    response = client.get(
        URL, query_string={"ingested_from": no_ponto.ingested_at.isoformat()}, headers=silver
    )

    assert [i["record_id"] for i in response.get_json()["items"]] == [str(no_ponto.record_id)]


@pytest.mark.parametrize("papel", [Role.USUARIO, Role.ADMIN], ids=["usuario", "admin"])
def test_quem_nao_e_silver_recebe_403(client, login, bronze, papel):
    """Nem o admin lê o bronze: menor privilégio sobre dado pessoal."""
    bronze.gravar(60)
    _, headers = login("alguem", role=papel)

    response = client.get(URL, headers=headers)

    assert response.status_code == 403
    assert bronze.chamadas == []


def test_o_403_vem_antes_da_validacao_da_query(client, headers, bronze):
    """Quem não lê o bronze não descobre o formato do cursor num 422."""
    response = client.get(URL, query_string={"cursor": "lixo"}, headers=headers)

    assert response.status_code == 403
    assert bronze.chamadas == []


def test_sem_token_responde_401(client):
    assert client.get(URL).status_code == 401


@pytest.mark.parametrize(
    ("parametros", "campo"),
    [
        pytest.param({"cursor": "lixo"}, "cursor", id="cursor malformado"),
        pytest.param({"limit": 0}, "limit", id="limite zero"),
        pytest.param({"limit": 1001}, "limit", id="acima do máximo"),
        pytest.param({"vin": "curto"}, "vin", id="VIN inválido"),
        pytest.param({"metric": "RPM"}, "metric", id="métrica fora do padrão"),
        pytest.param(
            {"collected_from": "2026-11-07T12:00:01Z", "collected_to": "2026-11-07T12:00:00Z"},
            "collected_from",
            id="período invertido",
        ),
        # O fuso leva a data para fora do calendário, e o BSON não a codifica.
        pytest.param(
            {"ingested_from": "0001-01-01T00:00:00+14:00"},
            "ingested_from",
            id="gravação antes do calendário",
        ),
        pytest.param(
            {"collected_from": "0001-01-01T00:00:00+14:00"},
            "collected_from",
            id="coleta antes do calendário",
        ),
        pytest.param(
            {"collected_to": "9999-12-31T23:59:59-14:00"},
            "collected_to",
            id="coleta depois do calendário",
        ),
    ],
)
def test_query_fora_do_contrato_responde_422(client, silver, bronze, parametros, campo):
    response = client.get(URL, query_string=parametros, headers=silver)

    assert response.status_code == 422, response.get_json()
    assert list(response.get_json()["errors"]["query"]) == [campo]
    assert bronze.chamadas == []


def test_bronze_fora_responde_503(client, silver, bronze):
    bronze.fora = True

    response = client.get(URL, headers={**silver, "X-Request-ID": "req-bronze-fora"})

    assert response.status_code == 503
    corpo = response.get_json()
    assert "bronze" in corpo["message"]
    assert corpo["request_id"] == "req-bronze-fora"
    assert "Retry-After" not in response.headers
