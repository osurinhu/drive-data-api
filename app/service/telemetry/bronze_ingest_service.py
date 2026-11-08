"""Gravação dos lotes no bronze (consumer): um documento por registro, uma vez só."""

import time
from dataclasses import dataclass
from uuid import UUID

from domain.contracts.telemetry_batch import TelemetryBatchV1
from domain.ports import BronzeObserver, BronzeRejectionReason, BronzeWriter, RejectionLog
from service.telemetry.bronze_rules import bronze_entry, bronze_rejection, has_canonical_vin


@dataclass(frozen=True, slots=True)
class BronzeIngestResult:
    inserted: int
    #: Os que já estavam no bronze, do mesmo remetente: reentrega da fila ou reenvio do
    #: lote pelo app.
    duplicates: int
    #: O ``id`` já estava no bronze, enviado por outra pessoa. O registro antigo fica.
    collisions: int
    #: Fora da forma que o producer publica.
    invalid: int


class BronzeIngestService:
    def __init__(
        self, writer: BronzeWriter, *, rejections: RejectionLog, observer: BronzeObserver
    ) -> None:
        self._writer = writer
        self._rejections = rejections
        self._observer = observer

    def ingest(self, batch: TelemetryBatchV1) -> BronzeIngestResult:
        """Grava os registros do lote que ainda não estão no bronze.

        É idempotente: a reentrega da mensagem e o reenvio do lote pelo app só
        completam o que faltava. O ``ingested_at`` vem do relógio do servidor do
        bronze, lido a cada tentativa: um carimbo tirado antes de uma espera cairia
        atrás de um cursor que a silver já passou.

        O que não se grava deixa rastro: o registro fora da forma e o ``id`` que já é de
        outro remetente vão para o ``RejectionLog``, e a mensagem segue para o ack. Levanta
        ``BronzeUnavailable`` quando o bronze não responde; repetir é seguro, porque a
        gravação e o rastro são idempotentes.
        """
        ingested_at = self._writer.server_time()
        stamped = time.monotonic()
        intact = [record for record in batch.records if has_canonical_vin(record)]
        invalid = [record for record in batch.records if not has_canonical_vin(record)]

        entries = [bronze_entry(batch, record, ingested_at) for record in intact]
        already_there = self._writer.insert_new(entries)
        stamp_age_seconds = time.monotonic() - stamped
        collided = self._collisions(already_there, sender=batch.sender.user_id)

        rejections = [
            bronze_rejection(batch, record, BronzeRejectionReason.INVALID_VIN, ingested_at)
            for record in invalid
        ] + [
            bronze_rejection(batch, record, BronzeRejectionReason.ID_COLLISION, ingested_at)
            for record in intact
            if record.record_id in collided
        ]
        self._rejections.record(rejections)

        result = BronzeIngestResult(
            inserted=len(entries) - len(already_there),
            duplicates=len(already_there) - len(collided),
            collisions=len(collided),
            invalid=len(invalid),
        )
        self._observer.batch_ingested(
            batch,
            ingested_at=ingested_at,
            stamp_age_seconds=stamp_age_seconds,
            inserted=result.inserted,
            duplicates=already_there - collided,
            collisions=collided,
            invalid={record.record_id for record in invalid},
        )
        return result

    def _collisions(self, already_there: set[UUID], *, sender: int) -> set[UUID]:
        """Dos ids que já estavam no bronze, os que são de outro remetente. O id que
        sumiu desde a gravação (a retenção o apagou) não tem com quem colidir."""
        if not already_there:
            return set()
        senders = self._writer.senders_of(already_there)
        return {record_id for record_id, other in senders.items() if other != sender}
