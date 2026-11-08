"""O lado da silver do pipeline, sem infraestrutura: a janela, o cursor e o codec dele.

O bronze é o dublê ``LeituraEmMemoria`` (``tests/conftest.py``), com a mesma semântica
do adaptador. O adaptador contra o Mongo de verdade está em
``tests/mongo/test_bronze_reader.py``.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from marshmallow import ValidationError

from domain.authz import ANY_OWNER
from domain.ports import BRONZE_START, BronzeEntry, BronzePosition
from repository.mongo.bronze_document import from_document, to_document
from schemas.bronze_schema import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    BronzeQuerySchema,
    decode_cursor,
    encode_cursor,
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
def servico(bronze):
    return BronzeReadService(bronze, settle_seconds=JANELA)


# --- O serviço ------------------------------------------------------------------


def test_sem_cursor_le_do_comeco_e_pede_um_registro_a_mais(servico, bronze):
    bronze.gravar(60)

    pagina = servico.read(ANY_OWNER, after=None, limit=10)

    assert len(pagina.items) == 1
    [chamada] = bronze.chamadas
    assert chamada["after"] == BRONZE_START
    assert chamada["limit"] == 11


def test_a_janela_deixa_o_gravado_ha_pouco_para_a_proxima_leitura(servico, bronze):
    antigo = bronze.gravar(JANELA + 10)
    bronze.gravar(JANELA - 10)

    pagina = servico.read(ANY_OWNER, after=None, limit=10)

    assert pagina.items == [antigo]
    assert pagina.watermark == bronze.agora - timedelta(seconds=JANELA)
    assert pagina.has_more is False


def test_has_more_e_o_cursor_apontam_para_onde_continuar(servico, bronze):
    registros = [bronze.gravar(90), bronze.gravar(80), bronze.gravar(70)]

    primeira = servico.read(ANY_OWNER, after=None, limit=2)
    segunda = servico.read(ANY_OWNER, after=primeira.next_cursor, limit=2)

    assert (primeira.items, primeira.has_more) == (registros[:2], True)
    assert primeira.next_cursor == _posicao(registros[1])
    assert (segunda.items, segunda.has_more) == (registros[2:], False)


@pytest.mark.parametrize("com_cursor", [False, True], ids=["sem cursor: o começo", "com cursor"])
def test_pagina_vazia_devolve_a_posicao_recebida(servico, bronze, com_cursor):
    posicao = BronzePosition(bronze.agora - timedelta(hours=1), uuid4()) if com_cursor else None

    pagina = servico.read(ANY_OWNER, after=posicao, limit=10)

    assert pagina.items == []
    assert pagina.has_more is False
    assert pagina.next_cursor == (posicao or BRONZE_START)


def test_le_tudo_uma_vez_so_mesmo_com_empate_no_milissegundo(servico, bronze):
    """O ``record_id`` desempata: com páginas de um, nenhum registro gravado no mesmo
    milissegundo que outro se perde ou se repete na troca de página."""
    registros = [bronze.gravar(90), *(bronze.gravar(60) for _ in range(3)), bronze.gravar(45)]

    lidos, cursor, mais = [], None, True
    while mais:
        pagina = servico.read(ANY_OWNER, after=cursor, limit=1)
        lidos += pagina.items
        cursor, mais = pagina.next_cursor, pagina.has_more

    assert len(lidos) == len(registros)
    assert {r.record_id for r in lidos} == {r.record_id for r in registros}


@pytest.mark.parametrize("escopo", [ANY_OWNER, 51], ids=["de todos", "de um dono"])
def test_o_escopo_de_dono_chega_ao_bronze(servico, bronze, escopo):
    bronze.gravar(60, user_id=51)
    bronze.gravar(60, user_id=52)

    pagina = servico.read(escopo, after=None, limit=10)

    assert bronze.chamadas[0]["dono"] is escopo
    assert {r.user_id for r in pagina.items} == ({51, 52} if escopo is ANY_OWNER else {51})


def test_bronze_fora_vira_servico_indisponivel(servico, bronze):
    bronze.gravar(60)
    bronze.fora = True

    with pytest.raises(ServiceUnavailableError, match="bronze") as erro:
        servico.read(ANY_OWNER, after=None, limit=10)

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
    cursor = encode_cursor(posicao)

    assert decode_cursor(cursor) == posicao
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
        pytest.param(_cursor({"v": 2, "t": 1, "id": ID}), id="versão desconhecida"),
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
    assert BronzeQuerySchema().load({}) == {"cursor": None, "limit": DEFAULT_LIMIT}


def test_a_query_decodifica_o_cursor():
    posicao = BronzePosition(INSTANTE, uuid4())

    query = BronzeQuerySchema().load({"cursor": encode_cursor(posicao), "limit": "7"})

    assert query == {"cursor": posicao, "limit": 7}


@pytest.mark.parametrize(
    ("parametros", "campo"),
    [
        pytest.param({"cursor": "lixo"}, "cursor", id="cursor malformado"),
        pytest.param({"limit": "0"}, "limit", id="limite zero"),
        pytest.param({"limit": str(MAX_LIMIT + 1)}, "limit", id="acima do máximo"),
    ],
)
def test_a_query_recusa_o_que_esta_fora_do_contrato(parametros, campo):
    with pytest.raises(ValidationError) as erro:
        BronzeQuerySchema().load(parametros)

    assert list(erro.value.messages) == [campo]


def test_a_mensagem_do_cursor_invalido_nao_explica_o_formato():
    """O cursor é opaco: a silver o devolve como o recebeu, e não o monta."""
    with pytest.raises(ValidationError) as erro:
        BronzeQuerySchema().load({"cursor": _cursor({"v": 2, "t": 1, "id": ID})})

    assert erro.value.messages == {"cursor": ["Cursor inválido."]}
