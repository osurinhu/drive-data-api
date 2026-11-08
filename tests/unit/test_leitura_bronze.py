"""O lado da silver do pipeline, sem infraestrutura: a janela, o cursor e o codec dele.

O bronze é o dublê ``LeituraEmMemoria`` (``tests/conftest.py``), com a mesma semântica
do adaptador. O adaptador contra o Mongo de verdade está em
``tests/mongo/test_bronze_reader.py``.
"""

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from marshmallow import ValidationError

from domain.authz import ANY_OWNER
from domain.ports import (
    BRONZE_START,
    NO_FILTERS,
    BronzeCursor,
    BronzeEntry,
    BronzeFilters,
    BronzePosition,
    position_at,
)
from repository.mongo.bronze_document import from_document, to_document
from schemas.bronze_schema import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_METRICS,
    OTHER_FILTER_MESSAGE,
    BronzeQuerySchema,
    ReceivedCursor,
    decode_cursor,
    encode_cursor,
    filters_fingerprint,
)
from service.errors import ServiceUnavailableError
from service.telemetry.bronze_read_service import BronzeReadService

JANELA = 30


def _posicao(registro: BronzeEntry) -> BronzePosition:
    return BronzePosition(ingested_at=registro.ingested_at, record_id=registro.record_id)


@pytest.fixture
def bronze(leitura_em_memoria):
    return leitura_em_memoria


@pytest.fixture
def servico(bronze, observador):
    return BronzeReadService(bronze, settle_seconds=JANELA, observer=observador)


def _ler(servico, escopo, *, after=None, ingested_from=None, filters=NO_FILTERS, limit=10):
    return servico.read(
        escopo, after=after, ingested_from=ingested_from, filters=filters, limit=limit
    )


# --- O serviço ------------------------------------------------------------------


def test_sem_cursor_le_do_comeco_e_pede_um_registro_a_mais(servico, bronze):
    bronze.gravar(60)

    pagina = _ler(servico, ANY_OWNER, after=None, limit=10)

    assert len(pagina.items) == 1
    [chamada] = bronze.chamadas
    assert chamada["after"] == BRONZE_START
    assert chamada["limit"] == 11


def test_a_janela_deixa_o_gravado_ha_pouco_para_a_proxima_leitura(servico, bronze):
    antigo = bronze.gravar(JANELA + 10)
    bronze.gravar(JANELA - 10)

    pagina = _ler(servico, ANY_OWNER, after=None, limit=10)

    assert pagina.items == [antigo]
    assert pagina.watermark == bronze.agora - timedelta(seconds=JANELA)
    assert pagina.has_more is False


def test_has_more_e_o_cursor_apontam_para_onde_continuar(servico, bronze):
    registros = [bronze.gravar(90), bronze.gravar(80), bronze.gravar(70)]

    primeira = _ler(servico, ANY_OWNER, after=None, limit=2)
    segunda = _ler(servico, ANY_OWNER, after=primeira.next_cursor.position, limit=2)

    assert (primeira.items, primeira.has_more) == (registros[:2], True)
    assert primeira.next_cursor == BronzeCursor(_posicao(registros[1]), NO_FILTERS)
    assert (segunda.items, segunda.has_more) == (registros[2:], False)


@pytest.mark.parametrize("com_cursor", [False, True], ids=["sem cursor: o começo", "com cursor"])
def test_pagina_vazia_devolve_a_posicao_recebida(servico, bronze, com_cursor):
    posicao = BronzePosition(bronze.agora - timedelta(hours=1), uuid4()) if com_cursor else None

    pagina = _ler(servico, ANY_OWNER, after=posicao, limit=10)

    assert pagina.items == []
    assert pagina.has_more is False
    assert pagina.next_cursor == BronzeCursor(posicao or BRONZE_START, NO_FILTERS)


def test_le_tudo_uma_vez_so_mesmo_com_empate_no_milissegundo(servico, bronze):
    """O ``record_id`` desempata: com páginas de um, nenhum registro gravado no mesmo
    milissegundo que outro se perde ou se repete na troca de página."""
    registros = [bronze.gravar(90), *(bronze.gravar(60) for _ in range(3)), bronze.gravar(45)]

    lidos, cursor, mais = [], None, True
    while mais:
        pagina = _ler(servico, ANY_OWNER, after=cursor, limit=1)
        lidos += pagina.items
        cursor, mais = pagina.next_cursor.position, pagina.has_more

    assert len(lidos) == len(registros)
    assert {r.record_id for r in lidos} == {r.record_id for r in registros}


@pytest.mark.parametrize("escopo", [ANY_OWNER, 51], ids=["de todos", "de um dono"])
def test_o_escopo_de_dono_chega_ao_bronze(servico, bronze, escopo):
    bronze.gravar(60, user_id=51)
    bronze.gravar(60, user_id=52)

    pagina = _ler(servico, escopo, after=None, limit=10)

    assert bronze.chamadas[0]["dono"] is escopo
    assert {r.user_id for r in pagina.items} == ({51, 52} if escopo is ANY_OWNER else {51})


def test_ingested_from_comeca_daquele_instante_inclusive(servico, bronze):
    antigo, no_ponto, depois = bronze.gravar(90), bronze.gravar(80), bronze.gravar(70)

    pagina = _ler(servico, ANY_OWNER, ingested_from=no_ponto.ingested_at)

    assert antigo not in pagina.items
    assert pagina.items == [no_ponto, depois]
    assert bronze.chamadas[0]["after"] == position_at(no_ponto.ingested_at)


def test_pagina_vazia_com_ingested_from_devolve_aquele_ponto(servico, bronze):
    ponto = bronze.agora - timedelta(hours=1)

    pagina = _ler(servico, ANY_OWNER, ingested_from=ponto)

    assert pagina.items == []
    assert pagina.next_cursor == BronzeCursor(position_at(ponto), NO_FILTERS)


def test_os_filtros_chegam_ao_bronze_e_voltam_no_cursor(servico, bronze):
    filtros = BronzeFilters(vin="9BWZZZ377VT004251", metrics=("speed",))
    procurado = bronze.gravar(60, vin=filtros.vin, metricas=("rpm", "speed"))
    bronze.gravar(60, metricas=("speed",))
    bronze.gravar(60, vin=filtros.vin, metricas=("rpm",))

    pagina = _ler(servico, ANY_OWNER, filters=filtros)

    assert pagina.items == [procurado]
    assert bronze.chamadas[0]["filtros"] == filtros
    assert pagina.next_cursor == BronzeCursor(_posicao(procurado), filtros)


def test_bronze_fora_vira_servico_indisponivel(servico, bronze):
    bronze.gravar(60)
    bronze.fora = True

    with pytest.raises(ServiceUnavailableError, match="bronze") as erro:
        _ler(servico, ANY_OWNER, after=None, limit=10)

    assert erro.value.retry_after is None


# --- O documento ----------------------------------------------------------------


def test_o_documento_lido_devolve_o_registro_gravado(bronze):
    """A ida (writer) e a volta (reader) do layout do bronze se desfazem."""
    registro = bronze.gravar(60)

    assert from_document(to_document(registro)) == registro


# --- O cursor -------------------------------------------------------------------

INSTANTE = datetime(2026, 11, 7, 14, 3, 11, 482000, tzinfo=UTC)


@pytest.mark.parametrize(
    "posicao",
    [
        pytest.param(BRONZE_START, id="o começo"),
        pytest.param(BronzePosition(INSTANTE, uuid4()), id="um registro"),
    ],
)
def test_o_cursor_volta_exatamente_a_posicao(posicao):
    cursor = encode_cursor(BronzeCursor(posicao, NO_FILTERS))

    assert decode_cursor(cursor) == ReceivedCursor(posicao, filters_fingerprint(NO_FILTERS))
    assert set(cursor) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _cursor(payload) -> str:
    texto = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(texto).rstrip(b"=").decode()


ID = "9f1c2a7e-6b4d-4d0e-9a51-2f6f1b0c8e11"


@pytest.mark.parametrize(
    "cursor",
    [
        pytest.param("", id="vazio"),
        pytest.param("não é base64", id="fora do alfabeto"),
        pytest.param(_cursor({}), id="objeto sem as chaves"),
        pytest.param(_cursor([1, 2, 3]), id="JSON que não é objeto"),
        pytest.param(base64.urlsafe_b64encode(b"\x80").decode(), id="sem UTF-8"),
        pytest.param(_cursor({"v": 3, "t": 1, "id": ID, "f": "x"}), id="versão desconhecida"),
        pytest.param(_cursor({"v": 2, "t": 1, "id": ID}), id="v2 sem a impressão"),
        pytest.param(_cursor({"v": 2, "t": 1, "id": ID, "f": 7}), id="impressão numérica"),
        pytest.param(_cursor({"v": True, "t": 1, "id": ID}), id="versão booleana"),
        pytest.param(_cursor({"v": 1, "t": -1, "id": ID}), id="instante negativo"),
        pytest.param(_cursor({"v": 1, "t": "1", "id": ID}), id="instante em texto"),
        pytest.param(_cursor({"v": 1, "t": 1.5, "id": ID}), id="instante fracionário"),
        pytest.param(_cursor({"v": 1, "t": 10**20, "id": ID}), id="instante fora do calendário"),
        pytest.param(_cursor({"v": 1, "t": 1, "id": "x"}), id="id sem UUID"),
        pytest.param(_cursor({"v": 1, "t": 1, "id": 7}), id="id numérico"),
        pytest.param(_cursor({"v": 1, "t": 1, "id": ID, "f": "x"}), id="chave a mais"),
    ],
)
def test_cursor_que_a_api_nao_emitiu_e_recusado(cursor):
    with pytest.raises(ValueError):
        decode_cursor(cursor)


def test_a_query_sem_parametros_le_do_comeco_com_o_limite_padrao():
    assert BronzeQuerySchema().load({}) == {
        "after": None,
        "ingested_from": None,
        "filters": NO_FILTERS,
        "limit": DEFAULT_LIMIT,
    }


def test_a_query_decodifica_o_cursor():
    posicao = BronzePosition(INSTANTE, uuid4())

    query = BronzeQuerySchema().load(
        {"cursor": encode_cursor(BronzeCursor(posicao, NO_FILTERS)), "limit": "7"}
    )

    assert query["after"] == posicao
    assert query["limit"] == 7


VIN = "9BWZZZ377VT004251"
_FILTROS = BronzeFilters(
    vin=VIN,
    metrics=("rpm", "speed"),
    collected_from=INSTANTE,
    collected_to=INSTANTE + timedelta(hours=1),
)
_QUERY = {
    "vin": VIN,
    "metric": ["rpm", "speed"],
    "collected_from": "2026-11-07T14:03:11.482+00:00",
    "collected_to": "2026-11-07T15:03:11.482+00:00",
}


def test_a_query_monta_os_filtros():
    assert BronzeQuerySchema().load(_QUERY)["filters"] == _FILTROS


def _cursor_de(filtros: BronzeFilters) -> str:
    return encode_cursor(BronzeCursor(BronzePosition(INSTANTE, uuid4()), filtros))


def test_o_cursor_vale_para_os_filtros_com_que_foi_emitido():
    query = BronzeQuerySchema().load({**_QUERY, "cursor": _cursor_de(_FILTROS)})

    assert query["filters"] == _FILTROS


@pytest.mark.parametrize(
    "outra_grafia",
    [
        pytest.param({"vin": VIN.lower()}, id="VIN em minúsculas"),
        pytest.param({"metric": ["speed", "rpm", "rpm"]}, id="métricas em outra ordem"),
        pytest.param({"collected_from": "2026-11-07T11:03:11.482-03:00"}, id="data em outro fuso"),
    ],
)
def test_o_mesmo_recorte_em_outra_grafia_e_o_mesmo_filtro(outra_grafia):
    query = BronzeQuerySchema().load({**_QUERY, **outra_grafia, "cursor": _cursor_de(_FILTROS)})

    assert query["filters"] == _FILTROS


@pytest.mark.parametrize(
    "mudanca",
    [
        pytest.param({"vin": "93YBSR7RHEJ298116"}, id="outro VIN"),
        pytest.param({"metric": ["rpm"]}, id="outras métricas"),
        pytest.param({"collected_from": "2026-11-07T14:03:12+00:00"}, id="outro início"),
        pytest.param({"collected_to": "2026-11-07T15:03:12+00:00"}, id="outro fim"),
    ],
)
def test_o_cursor_de_outro_filtro_e_recusado(mudanca):
    with pytest.raises(ValidationError) as erro:
        BronzeQuerySchema().load({**_QUERY, **mudanca, "cursor": _cursor_de(_FILTROS)})

    assert erro.value.messages == {"cursor": [OTHER_FILTER_MESSAGE]}


def test_cursor_com_filtro_nao_serve_para_a_leitura_sem_filtro():
    with pytest.raises(ValidationError) as erro:
        BronzeQuerySchema().load({"cursor": _cursor_de(_FILTROS)})

    assert erro.value.messages == {"cursor": [OTHER_FILTER_MESSAGE]}


def test_o_cursor_v1_vale_como_a_leitura_sem_filtro():
    """A silver pode ter guardado um cursor de antes dos filtros."""
    v1 = _cursor({"v": 1, "t": 1762524191482, "id": ID})

    assert BronzeQuerySchema().load({"cursor": v1})["after"].record_id == UUID(ID)
    with pytest.raises(ValidationError) as erro:
        BronzeQuerySchema().load({"cursor": v1, "vin": VIN})
    assert erro.value.messages == {"cursor": [OTHER_FILTER_MESSAGE]}


@pytest.mark.parametrize(
    ("parametros", "campo"),
    [
        pytest.param({"cursor": "lixo"}, "cursor", id="cursor malformado"),
        pytest.param({"limit": "0"}, "limit", id="limite zero"),
        pytest.param({"limit": str(MAX_LIMIT + 1)}, "limit", id="acima do máximo"),
        pytest.param({"vin": "93YBSR7RHEJ29811O"}, "vin", id="VIN com O"),
        pytest.param({"metric": ["RPM"]}, "metric", id="métrica fora do padrão"),
        pytest.param(
            {"metric": [f"m{i}" for i in range(MAX_METRICS + 1)]}, "metric", id="métricas demais"
        ),
        pytest.param({"collected_from": "2026-11-07T12:00:00"}, "collected_from", id="sem fuso"),
        pytest.param(
            {"ingested_from": "2026-11-07T12:00:00"}, "ingested_from", id="início sem fuso"
        ),
        pytest.param(
            {
                "collected_from": "2026-11-07T12:00:01+00:00",
                "collected_to": "2026-11-07T12:00:00+00:00",
            },
            "collected_from",
            id="período invertido",
        ),
        pytest.param(
            {
                "ingested_from": "2026-11-07T12:00:00+00:00",
                "cursor": encode_cursor(BronzeCursor(BRONZE_START, NO_FILTERS)),
            },
            "ingested_from",
            id="início junto com o cursor",
        ),
    ],
)
def test_a_query_recusa_o_que_esta_fora_do_contrato(parametros, campo):
    with pytest.raises(ValidationError) as erro:
        BronzeQuerySchema().load(parametros)

    assert list(erro.value.messages) == [campo]


def test_a_mensagem_do_cursor_invalido_nao_explica_o_formato():
    """O cursor é opaco: a silver o devolve como o recebeu, e não o monta."""
    with pytest.raises(ValidationError) as erro:
        BronzeQuerySchema().load({"cursor": _cursor({"v": 9, "t": 1, "id": ID})})

    assert erro.value.messages == {"cursor": ["Cursor inválido."]}


def test_o_atraso_da_silver_e_o_que_falta_ate_a_marca_dagua(servico, bronze, observador):
    bronze.gravar(JANELA + 100)
    bronze.gravar(JANELA + 50)

    _ler(servico, ANY_OWNER, limit=1)

    assert observador.de("page_read") == [{"args": (), "records": 1, "lag_seconds": 100.0}]


def test_sem_mais_paginas_o_atraso_e_zero(servico, bronze, observador):
    bronze.gravar(JANELA + 100)

    _ler(servico, ANY_OWNER, limit=10)

    assert observador.de("page_read") == [{"args": (), "records": 1, "lag_seconds": 0.0}]


def test_a_leitura_com_filtro_nao_mede_atraso(servico, bronze, observador):
    bronze.gravar(JANELA + 100)
    filtros = replace(NO_FILTERS, vin="93YBSR7RHEJ298116")

    _ler(servico, ANY_OWNER, filters=filtros, limit=10)

    assert observador.de("page_read")[0]["lag_seconds"] is None
