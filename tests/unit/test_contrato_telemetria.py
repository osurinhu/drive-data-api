"""O contrato ``telemetry.batch.v1``: o formato que atravessa a fila.

O exemplo canônico (``tests/data/telemetry_batch_v1.json``) fixa o formato no
fio. Se ele precisar mudar para um teste passar, a mudança quebra consumers que
já existem: é a v2, não um ajuste.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from domain.contracts.telemetry_batch import (
    SENDER_RELATIONS,
    ContractError,
    Sender,
    TelemetryBatchV1,
    TelemetryRecordV1,
    decode,
    encode,
)
from domain.ports import SenderRelation

EXEMPLO = Path(__file__).resolve().parents[1] / "data" / "telemetry_batch_v1.json"


def _exemplo() -> dict:
    return json.loads(EXEMPLO.read_text(encoding="utf-8"))


def _corpo(envelope: dict) -> bytes:
    return json.dumps(envelope).encode("utf-8")


def test_o_exemplo_canonico_decodifica():
    lote = decode(EXEMPLO.read_bytes())

    assert lote.message_id == UUID("4b0f6c1e-0a8e-4f43-9a36-0c7f3b2a9d10")
    assert lote.received_at == datetime(2026, 11, 2, 14, 5, 0, 118000, tzinfo=UTC)
    assert lote.sender == Sender(user_id=51)
    [registro] = lote.records
    assert registro.vin == "93YBSR7RHEJ298116"
    assert registro.vehicle_id == 7
    assert registro.sender_relation == "driver"
    assert registro.payload_user_matches_sender is False
    assert registro.raw == _exemplo()["records"][0]["raw"]


def test_codificar_o_exemplo_devolve_o_mesmo_json():
    """O encoder escreve exatamente o formato do exemplo: nomes, aninhamento e datas."""
    assert json.loads(encode(decode(EXEMPLO.read_bytes()))) == _exemplo()


def test_ida_e_volta_preserva_o_lote():
    lote = TelemetryBatchV1(
        message_id=UUID("00000000-0000-4000-8000-000000000001"),
        received_at=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
        request_id=None,
        sender=Sender(user_id=1),
        records=(
            TelemetryRecordV1(
                record_id=UUID("00000000-0000-4000-8000-000000000002"),
                vin="9BWZZZ377VT004251",
                vehicle_id=3,
                sender_relation="owner",
                collected_at=datetime(2026, 11, 7, 11, 59, tzinfo=UTC),
                payload_user_matches_sender=True,
                raw={"texto": "çã ✓"},
            ),
        ),
    )

    assert decode(encode(lote)) == lote


def test_o_corpo_e_utf8_sem_escape():
    lote = decode(EXEMPLO.read_bytes())
    registro = replace(lote.records[0], raw={"apelido": "Fusca do João"})

    assert "Fusca do João".encode() in encode(replace(lote, records=(registro,)))


def test_campo_desconhecido_e_ignorado():
    """Tolerant reader: o producer pode ganhar campo opcional sem quebrar o consumer."""
    envelope = _exemplo()
    envelope["campo_novo"] = "x"
    envelope["records"][0]["outro_campo"] = 1

    assert decode(_corpo(envelope)) == decode(EXEMPLO.read_bytes())


@pytest.mark.parametrize("versao", [2, 0, "1", True, None])
def test_versao_que_nao_e_a_v1_e_recusada(versao):
    """``True`` entra na lista porque ``True == 1`` em Python."""
    envelope = _exemplo()
    envelope["schema_version"] = versao

    with pytest.raises(ContractError, match="schema_version"):
        decode(_corpo(envelope))


@pytest.mark.parametrize(
    "corpo",
    [b"nao e json", b"\xff\xfe", b"[1, 2]"],
    ids=["texto", "nao e utf8", "lista"],
)
def test_corpo_que_nao_e_um_objeto_json_e_recusado(corpo):
    with pytest.raises(ContractError):
        decode(corpo)


def _sem(campo: str) -> dict:
    envelope = _exemplo()
    del envelope[campo]
    return envelope


def _registro_com(**campos) -> dict:
    envelope = _exemplo()
    envelope["records"][0].update(campos)
    return envelope


@pytest.mark.parametrize(
    "envelope",
    [
        pytest.param(_sem("message_id"), id="sem message_id"),
        pytest.param(_sem("sender"), id="sem sender"),
        pytest.param({**_exemplo(), "records": []}, id="sem registros"),
        pytest.param(_registro_com(collected_at="2026-11-02T14:03:11"), id="data sem fuso"),
        pytest.param(_registro_com(sender_relation="fleet"), id="vínculo desconhecido"),
        pytest.param(_registro_com(vehicle_id="7"), id="vehicle_id em texto"),
    ],
)
def test_envelope_fora_do_contrato_e_recusado(envelope):
    with pytest.raises(ContractError, match="fora do contrato"):
        decode(_corpo(envelope))


def test_os_vinculos_da_v1_sao_os_do_dominio():
    """Falhou? Um vínculo mudou em ``SenderRelation`` e não no contrato, ou o
    contrário. A v1 não muda por tabela: decida se é valor novo (o consumer
    precisa conhecê-lo antes) ou a v2."""
    assert set(SENDER_RELATIONS) == {relation.value for relation in SenderRelation}
