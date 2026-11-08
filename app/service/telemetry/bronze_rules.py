"""Regras puras do consumer: do registro do contrato ao registro do bronze."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from domain.contracts.telemetry_batch import TelemetryBatchV1, TelemetryRecordV1
from domain.ports import BronzeEntry


def available_metrics(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """As métricas com leitura disponível (``available`` verdadeiro), ordenadas e sem
    repetição.

    A leitura que não se deixa ler é ignorada, sem reprovar o registro: o bruto
    é guardado como veio, e a lista só serve ao filtro por métrica.
    """
    readings = raw.get("readings")
    if not isinstance(readings, list):
        return ()
    metrics = {
        reading["metric"]
        for reading in readings
        if isinstance(reading, Mapping)
        and reading.get("available") is True
        and isinstance(reading.get("metric"), str)
    }
    return tuple(sorted(metrics))


def bronze_entry(
    batch: TelemetryBatchV1, record: TelemetryRecordV1, ingested_at: datetime
) -> BronzeEntry:
    """O registro do lote como o bronze o guarda. O remetente é o do envelope (o
    usuário do token, no producer), nunca o ``user`` do bruto."""
    return BronzeEntry(
        record_id=record.record_id,
        vin=record.vin,
        vehicle_id=record.vehicle_id,
        user_id=batch.sender.user_id,
        sender_relation=record.sender_relation,
        payload_user_matches_sender=record.payload_user_matches_sender,
        collected_at=record.collected_at,
        available_metrics=available_metrics(record.raw),
        received_at=batch.received_at,
        ingested_at=ingested_at,
        message_id=batch.message_id,
        request_id=batch.request_id,
        schema_version=batch.schema_version,
        raw=record.raw,
    )
