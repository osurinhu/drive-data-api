"""O lado do consumer do pipeline, sem infraestrutura: do envelope ao registro do bronze.

O bronze é um dublê em memória (``BronzeEmMemoria``), com a mesma semântica do
adaptador: grava pelo ``record_id`` e devolve os que já existiam.
"""

from datetime import UTC, datetime

import pytest

from domain.ports import BronzeEntry, BronzeUnavailable
from service.telemetry.bronze_ingest_service import BronzeIngestService
from service.telemetry.bronze_rules import available_metrics

HORA_DO_SERVIDOR = datetime(2026, 11, 7, 15, 0, 0, 950000, tzinfo=UTC)


class BronzeEmMemoria:
    def __init__(self) -> None:
        self.gravados: dict = {}
        self.horas_lidas = 0
        self.fora = False

    def server_time(self) -> datetime:
        if self.fora:
            raise BronzeUnavailable("Mongo fora (dublê)")
        self.horas_lidas += 1
        return HORA_DO_SERVIDOR

    def insert_new(self, entries) -> set:
        ja_estavam = {entry.record_id for entry in entries if entry.record_id in self.gravados}
        for entry in entries:
            self.gravados.setdefault(entry.record_id, entry)
        return ja_estavam


@pytest.fixture
def bronze():
    return BronzeEmMemoria()


@pytest.fixture
def servico(bronze):
    return BronzeIngestService(bronze)


def test_cada_registro_vira_uma_entrada_com_o_remetente_do_envelope(servico, bronze, novo_lote):
    lote = novo_lote(registros=2, sender_id=42)

    resultado = servico.ingest(lote)

    assert (resultado.inserted, resultado.duplicates) == (2, 0)
    registro = lote.records[0]
    assert bronze.gravados[registro.record_id] == BronzeEntry(
        record_id=registro.record_id,
        vin=registro.vin,
        vehicle_id=registro.vehicle_id,
        user_id=42,
        sender_relation=registro.sender_relation,
        payload_user_matches_sender=registro.payload_user_matches_sender,
        collected_at=registro.collected_at,
        available_metrics=("rpm",),
        received_at=lote.received_at,
        ingested_at=HORA_DO_SERVIDOR,
        message_id=lote.message_id,
        request_id=lote.request_id,
        schema_version=1,
        raw=registro.raw,
    )


def test_regravar_o_lote_nao_duplica_e_conta_os_que_ja_estavam(servico, bronze, novo_lote):
    """A reentrega da fila e o reenvio do app trazem os mesmos ids."""
    lote = novo_lote(registros=3)
    servico.ingest(lote)

    resultado = servico.ingest(lote)

    assert (resultado.inserted, resultado.duplicates) == (0, 3)
    assert len(bronze.gravados) == 3


def test_a_hora_do_servidor_e_lida_a_cada_tentativa(servico, bronze, novo_lote):
    """Um carimbo guardado de uma tentativa para a outra cairia atrás do cursor da silver."""
    lote = novo_lote()

    servico.ingest(lote)
    servico.ingest(lote)

    assert bronze.horas_lidas == 2


def test_bronze_fora_sobe_bronze_indisponivel_sem_gravar(servico, bronze, novo_lote):
    bronze.fora = True

    with pytest.raises(BronzeUnavailable):
        servico.ingest(novo_lote())

    assert bronze.gravados == {}


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [
        pytest.param(
            {
                "readings": [
                    {"metric": "speed", "available": True, "value": 40},
                    {"metric": "rpm", "available": True, "value": 900},
                    {"metric": "maf", "available": False, "value": None},
                    {"metric": "rpm", "available": True, "value": 901},
                ]
            },
            ("rpm", "speed"),
            id="só as disponíveis, em ordem e sem repetir",
        ),
        pytest.param({}, (), id="sem readings"),
        pytest.param({"readings": "rpm"}, (), id="readings que não é lista"),
        pytest.param(
            {"readings": ["rpm", {"metric": 7, "available": True}]},
            (),
            id="leitura fora do formato",
        ),
        pytest.param(
            {"readings": [{"metric": "rpm", "available": "true"}]},
            (),
            id="available que não é booleano",
        ),
    ],
)
def test_as_metricas_disponiveis(raw, esperado):
    assert available_metrics(raw) == esperado
