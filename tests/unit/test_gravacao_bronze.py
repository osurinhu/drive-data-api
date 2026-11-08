"""O lado do consumer do pipeline, sem infraestrutura: do envelope ao registro do bronze.

O bronze é um dublê em memória (``BronzeEmMemoria``), com a mesma semântica do
adaptador: grava pelo ``record_id`` e devolve os que já existiam. As recusas ficam no
``RecusasEmMemoria``.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from domain.ports import BronzeEntry, BronzeRejection, BronzeRejectionReason, BronzeUnavailable
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

    def senders_of(self, record_ids) -> dict:
        return {i: self.gravados[i].user_id for i in record_ids if i in self.gravados}


class RecusasEmMemoria:
    def __init__(self) -> None:
        self.recusas: list[BronzeRejection] = []
        self.fora = False

    def record(self, rejections) -> None:
        if self.fora:
            raise BronzeUnavailable("Mongo fora (dublê)")
        self.recusas += rejections


@pytest.fixture
def bronze():
    return BronzeEmMemoria()


@pytest.fixture
def recusas():
    return RecusasEmMemoria()


@pytest.fixture
def servico(bronze, recusas):
    return BronzeIngestService(bronze, rejections=recusas)


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


def _com_o_id_de(lote, original):
    """O lote com o id do primeiro registro de outro lote."""
    registro = replace(lote.records[0], record_id=original.records[0].record_id)
    return replace(lote, records=(registro,))


def test_o_mesmo_remetente_reenviando_e_duplicado_e_nao_deixa_recusa(servico, recusas, novo_lote):
    original = novo_lote(sender_id=51)
    servico.ingest(original)

    resultado = servico.ingest(_com_o_id_de(novo_lote(sender_id=51), original))

    assert (resultado.duplicates, resultado.collisions) == (1, 0)
    assert recusas.recusas == []


def test_o_id_de_outro_remetente_e_colisao_e_o_registro_antigo_fica(
    servico, bronze, recusas, novo_lote
):
    original = novo_lote(sender_id=51)
    servico.ingest(original)
    outro = _com_o_id_de(novo_lote(sender_id=52), original)

    resultado = servico.ingest(outro)

    assert (resultado.inserted, resultado.duplicates, resultado.collisions) == (0, 0, 1)
    registro = original.records[0]
    assert bronze.gravados[registro.record_id].user_id == 51
    assert recusas.recusas == [
        BronzeRejection(
            record_id=registro.record_id,
            vin=outro.records[0].vin,
            user_id=52,
            reason=BronzeRejectionReason.ID_COLLISION,
            message_id=outro.message_id,
            request_id=outro.request_id,
            received_at=outro.received_at,
            rejected_at=HORA_DO_SERVIDOR,
            schema_version=1,
            raw=outro.records[0].raw,
        )
    ]


def test_lote_misto_grava_o_novo_e_separa_duplicado_de_colisao(servico, recusas, novo_lote):
    do_mesmo = novo_lote(sender_id=52)
    de_outro = novo_lote(sender_id=51)
    servico.ingest(do_mesmo)
    servico.ingest(de_outro)
    lote = novo_lote(registros=3, sender_id=52)
    registros = (
        replace(lote.records[0], record_id=do_mesmo.records[0].record_id),
        replace(lote.records[1], record_id=de_outro.records[0].record_id),
        lote.records[2],
    )

    resultado = servico.ingest(replace(lote, records=registros))

    assert (resultado.inserted, resultado.duplicates, resultado.collisions) == (1, 1, 1)
    assert [r.record_id for r in recusas.recusas] == [de_outro.records[0].record_id]


def test_id_que_a_retencao_ja_apagou_nao_colide(servico, bronze, recusas, novo_lote):
    """Entre a gravação recusar o id e a consulta do remetente, o documento expirou."""
    lote = novo_lote()
    bronze.insert_new = lambda entries: {entry.record_id for entry in entries}

    resultado = servico.ingest(lote)

    assert (resultado.duplicates, resultado.collisions) == (1, 0)
    assert recusas.recusas == []


@pytest.mark.parametrize(
    "vin",
    [
        pytest.param("93YBSR7RHEJ29811O", id="com O"),
        pytest.param("93YBSR7RHEJ2981", id="curto"),
        pytest.param("93ybsr7rhej298116", id="em minúsculas"),
        pytest.param(" 93YBSR7RHEJ298116", id="com espaço"),
    ],
)
def test_vin_fora_da_forma_e_recusado_e_o_resto_do_lote_e_gravado(
    servico, bronze, recusas, novo_lote, vin
):
    """O producer nunca publicaria isso: é bug ou versão divergente, e não vira lixo no
    bronze nem reprova os outros registros."""
    lote = novo_lote(registros=2)
    invalido, valido = replace(lote.records[0], vin=vin), lote.records[1]

    resultado = servico.ingest(replace(lote, records=(invalido, valido)))

    assert (resultado.inserted, resultado.invalid) == (1, 1)
    assert set(bronze.gravados) == {valido.record_id}
    [recusa] = recusas.recusas
    assert (recusa.record_id, recusa.vin) == (invalido.record_id, vin)
    assert recusa.reason is BronzeRejectionReason.INVALID_VIN


def test_lote_sem_recusa_nao_deixa_rastro(servico, recusas, novo_lote):
    servico.ingest(novo_lote(registros=2))

    assert recusas.recusas == []


def test_sem_gravar_a_recusa_a_mensagem_nao_recebe_ack(servico, recusas, novo_lote):
    """O ``BronzeUnavailable`` devolve a mensagem à fila: o ack perderia o único rastro."""
    recusas.fora = True
    lote = novo_lote()
    invalido = replace(lote, records=(replace(lote.records[0], vin="invalido"),))

    with pytest.raises(BronzeUnavailable):
        servico.ingest(invalido)


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
