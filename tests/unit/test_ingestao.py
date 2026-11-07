"""O producer sem infraestrutura: as regras de partição e o serviço, com dublês.

A posse é um dicionário em memória e a fila é o ``FilaEmMemoria`` do conftest.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from domain.ports import AuthorizedVehicle, SenderRelation
from service.errors import ServiceUnavailableError
from service.telemetry.ingest_rules import (
    IncomingRecord,
    RejectionReason,
    partition_by_authorization,
    payload_user_matches_sender,
)
from service.telemetry.ingest_service import UNAVAILABLE_MESSAGE, TelemetryIngestService

MEU = "93YBSR7RHEJ298116"
ALHEIO = "9BWZZZ377VT004251"
COLETA = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


class PosseEmMemoria:
    """Dublê do ``VehicleAuthorization``: os VINs do remetente, e as consultas feitas."""

    def __init__(self, vins: dict[str, AuthorizedVehicle]) -> None:
        self.vins = vins
        self.consultas: list[tuple[int, set[str]]] = []

    def authorized_vins(self, user_id, vins):
        self.consultas.append((user_id, set(vins)))
        return {vin: self.vins[vin] for vin in vins if vin in self.vins}


def _registro(vin: str, **raw_extra) -> dict:
    """Um registro como o schema o entrega ao serviço."""
    record_id = uuid4()
    raw = {"id": str(record_id), "vin": vin, "collected_at": "2026-11-07T12:00:00Z", **raw_extra}
    return {"id": record_id, "vin": vin, "collected_at": COLETA, "raw": raw}


def _servico(fila, vins=None, retry_after=30):
    posse = PosseEmMemoria(vins or {MEU: AuthorizedVehicle(7, SenderRelation.OWNER)})
    return TelemetryIngestService(posse, fila, retry_after), posse


# --- Regras -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [({"user": "joao"}, True), ({"user": "admin"}, False), ({}, False), ({"user": None}, False)],
    ids=["mesmo login", "outro login", "sem o campo", "nulo"],
)
def test_payload_user_matches_sender(raw, esperado):
    assert payload_user_matches_sender(raw, "joao") is esperado


def test_a_particao_guarda_a_ordem_e_a_posicao_dos_rejeitados():
    registros = [
        IncomingRecord.from_payload(_registro(ALHEIO)),
        IncomingRecord.from_payload(_registro(MEU)),
        IncomingRecord.from_payload(_registro(ALHEIO)),
    ]
    autorizados = {MEU: AuthorizedVehicle(7, SenderRelation.OWNER)}

    aceitos, rejeitados = partition_by_authorization(registros, autorizados, "joao")

    assert [a.record_id for a in aceitos] == [registros[1].record_id]
    assert aceitos[0].vehicle_id == 7
    assert aceitos[0].sender_relation == "owner"
    assert [(r.index, r.id, r.reason) for r in rejeitados] == [
        (0, registros[0].record_id, RejectionReason.VEHICLE_NOT_AUTHORIZED),
        (2, registros[2].record_id, RejectionReason.VEHICLE_NOT_AUTHORIZED),
    ]


def test_o_registro_entra_na_forma_canonica_e_o_bruto_fica_como_veio():
    registro = IncomingRecord.from_payload(_registro(f" {MEU.lower()} "))

    assert registro.vin == MEU
    assert registro.raw["vin"] == f" {MEU.lower()} "


# --- Serviço ----------------------------------------------------------------------


def test_lote_misto_publica_um_envelope_so_com_os_aceitos(fila_em_memoria):
    servico, _ = _servico(fila_em_memoria)
    registros = [_registro(MEU, user="joao"), _registro(ALHEIO)]
    antes = datetime.now(UTC)

    recibo = servico.ingest(sender_id=51, sender_name="joao", records=registros, request_id="req-1")

    [lote] = fila_em_memoria.publicados
    assert recibo.batch_id == lote.message_id
    assert recibo.accepted == 1
    assert [(r.index, r.vin) for r in recibo.rejected] == [(1, ALHEIO)]
    assert antes <= recibo.received_at <= antes + timedelta(seconds=5)
    assert lote.received_at == recibo.received_at
    assert lote.sender.user_id == 51
    assert lote.request_id == "req-1"
    [registro] = lote.records
    assert registro.record_id == registros[0]["id"]
    assert registro.payload_user_matches_sender is True
    assert registro.raw == registros[0]["raw"]


def test_a_posse_e_consultada_uma_vez_por_lote_com_os_vins_canonicos(fila_em_memoria):
    servico, posse = _servico(fila_em_memoria)
    registros = [_registro(MEU.lower()), _registro(MEU), _registro(ALHEIO)]

    servico.ingest(sender_id=51, sender_name="joao", records=registros, request_id=None)

    assert posse.consultas == [(51, {MEU, ALHEIO})]
    assert len(fila_em_memoria.publicados[0].records) == 2


def test_nada_aceito_nao_publica_e_o_recibo_vem_sem_batch_id(fila_em_memoria):
    servico, _ = _servico(fila_em_memoria)

    recibo = servico.ingest(
        sender_id=51, sender_name="joao", records=[_registro(ALHEIO)], request_id=None
    )

    assert fila_em_memoria.publicados == []
    assert recibo.batch_id is None
    assert recibo.accepted == 0
    assert len(recibo.rejected) == 1


def test_fila_sem_confirmacao_vira_servico_indisponivel_com_o_prazo(fila_em_memoria):
    fila_em_memoria.falhar = True
    servico, _ = _servico(fila_em_memoria, retry_after=42)

    with pytest.raises(ServiceUnavailableError, match="indisponível") as erro:
        servico.ingest(sender_id=51, sender_name="joao", records=[_registro(MEU)], request_id=None)

    assert erro.value.retry_after == 42
    assert str(erro.value) == UNAVAILABLE_MESSAGE


def test_cada_lote_publicado_tem_um_id_novo(fila_em_memoria):
    servico, _ = _servico(fila_em_memoria)

    for _ in range(2):
        servico.ingest(sender_id=51, sender_name="joao", records=[_registro(MEU)], request_id=None)

    ids = {lote.message_id for lote in fila_em_memoria.publicados}
    assert len(ids) == 2
    assert all(isinstance(i, UUID) for i in ids)
