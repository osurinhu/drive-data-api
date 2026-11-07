"""Regras puras do producer: que registros entram no envelope e quais voltam ao app.

Sem I/O e sem estado: recebem o lote já validado pelo schema e o resultado da
consulta de posse, e devolvem a partição.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from domain.contracts.telemetry_batch import TelemetryRecordV1
from domain.ports import AuthorizedVehicle
from domain.vin import normalize_vin


class RejectionReason(StrEnum):
    #: Um motivo só, para qualquer VIN sem vínculo com o remetente: dizer "é de
    #: outra pessoa" revelaria que o VIN está cadastrado.
    VEHICLE_NOT_AUTHORIZED = "vehicle_not_authorized"


@dataclass(frozen=True, slots=True)
class IncomingRecord:
    """Um registro do lote, como o serviço o usa. O VIN já está na forma canônica."""

    record_id: UUID
    vin: str
    collected_at: datetime
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, record: Mapping[str, Any]) -> "IncomingRecord":
        """Do registro que o schema validou (``id``, ``vin``, ``collected_at``, ``raw``)."""
        return cls(
            record_id=record["id"],
            vin=normalize_vin(record["vin"]),
            collected_at=record["collected_at"],
            raw=record["raw"],
        )


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """Registro que não foi publicado. Os campos são os do recibo (``id``, e não
    ``record_id``), porque ``schemas.telemetry_schema`` os espelha."""

    index: int
    id: UUID
    vin: str
    reason: RejectionReason


def payload_user_matches_sender(raw: Mapping[str, Any], sender_name: str) -> bool:
    """O campo ``user`` do registro bruto é o login de quem enviou?

    Só informa: a posse vem do token. Falso também quando o campo não veio.
    """
    return raw.get("user") == sender_name


def partition_by_authorization(
    records: Sequence[IncomingRecord],
    authorized: Mapping[str, AuthorizedVehicle],
    sender_name: str,
) -> tuple[tuple[TelemetryRecordV1, ...], tuple[RejectedRecord, ...]]:
    """Separa os registros pelos quais o remetente pode enviar dos que voltam ao app.

    Os aceitos saem já no formato do contrato, com o vínculo que autorizou o envio;
    os rejeitados guardam a posição no lote (``index``), para o app saber qual reenviar.
    """
    accepted: list[TelemetryRecordV1] = []
    rejected: list[RejectedRecord] = []
    for index, record in enumerate(records):
        vehicle = authorized.get(record.vin)
        if vehicle is None:
            rejected.append(
                RejectedRecord(
                    index=index,
                    id=record.record_id,
                    vin=record.vin,
                    reason=RejectionReason.VEHICLE_NOT_AUTHORIZED,
                )
            )
            continue
        accepted.append(
            TelemetryRecordV1(
                record_id=record.record_id,
                vin=record.vin,
                vehicle_id=vehicle.vehicle_id,
                sender_relation=vehicle.relation.value,
                collected_at=record.collected_at,
                payload_user_matches_sender=payload_user_matches_sender(record.raw, sender_name),
                raw=record.raw,
            )
        )
    return tuple(accepted), tuple(rejected)
