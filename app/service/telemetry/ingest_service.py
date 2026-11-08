"""Recebimento dos lotes do app (producer): posse por registro e publicação na fila."""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from domain.contracts.telemetry_batch import Sender, TelemetryBatchV1
from domain.ports import (
    IngestObserver,
    PublishFailed,
    TelemetryPublisher,
    VehicleAuthorization,
)
from service.errors import ServiceUnavailableError
from service.telemetry.ingest_rules import (
    IncomingRecord,
    RejectedRecord,
    partition_by_authorization,
)

UNAVAILABLE_MESSAGE = "Ingestão temporariamente indisponível. Tente novamente em instantes."


@dataclass(frozen=True, slots=True)
class IngestReceipt:
    """O recibo do lote (o 202). ``schemas.telemetry_schema.BatchReceiptSchema`` o
    espelha, e ``tests/test_contracts.py`` reprova se os dois divergirem."""

    #: O ``message_id`` do envelope publicado; ``None`` quando nada foi aceito.
    batch_id: UUID | None
    received_at: datetime
    accepted: int
    rejected: list[RejectedRecord]


class TelemetryIngestService:
    def __init__(
        self,
        authorization: VehicleAuthorization,
        publisher: TelemetryPublisher,
        retry_after_seconds: int,
        *,
        observer: IngestObserver,
    ) -> None:
        self._authorization = authorization
        self._publisher = publisher
        self._retry_after_seconds = retry_after_seconds
        self._observer = observer

    def ingest(
        self,
        *,
        sender_id: int,
        sender_name: str,
        records: Sequence[Mapping[str, Any]],
        request_id: str | None,
    ) -> IngestReceipt:
        """Publica, num envelope só, os registros de veículos pelos quais o remetente
        pode enviar, e devolve os outros no recibo.

        A posse vale no momento do envio, numa consulta só para o lote inteiro. Só
        volta depois da confirmação do broker; sem ela, nada é dado como aceito e a
        resposta é ``ServiceUnavailableError`` (o app guarda o lote e reenvia).
        """
        received_at = datetime.now(UTC)
        incoming = [IncomingRecord.from_payload(record) for record in records]
        authorized = self._authorization.authorized_vins(
            sender_id, {record.vin for record in incoming}
        )
        accepted, rejected = partition_by_authorization(incoming, authorized, sender_name)

        if not accepted:
            self._report_rejected(rejected)
            self._observer.nothing_accepted()
            return IngestReceipt(
                batch_id=None, received_at=received_at, accepted=0, rejected=list(rejected)
            )

        batch = TelemetryBatchV1(
            message_id=uuid4(),
            received_at=received_at,
            request_id=request_id,
            sender=Sender(user_id=sender_id),
            records=accepted,
        )
        started = time.monotonic()
        try:
            self._publisher.publish(batch)
        except PublishFailed as error:
            self._observer.publish_failed(batch, reason=str(error))
            raise ServiceUnavailableError(
                UNAVAILABLE_MESSAGE, retry_after=self._retry_after_seconds
            ) from error

        self._observer.batch_published(batch, publish_seconds=time.monotonic() - started)
        self._report_rejected(rejected)
        return IngestReceipt(
            batch_id=batch.message_id,
            received_at=received_at,
            accepted=len(accepted),
            rejected=list(rejected),
        )

    def _report_rejected(self, rejected: Sequence[RejectedRecord]) -> None:
        for record in rejected:
            self._observer.record_rejected(
                record_id=record.id, vin=record.vin, reason=str(record.reason)
            )
