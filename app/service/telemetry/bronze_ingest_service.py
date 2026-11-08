"""Gravação dos lotes no bronze (consumer): um documento por registro, uma vez só."""

import logging
from dataclasses import dataclass

from domain.contracts.telemetry_batch import TelemetryBatchV1
from domain.ports import BronzeWriter
from service.telemetry.bronze_rules import bronze_entry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BronzeIngestResult:
    inserted: int
    #: Os que já estavam no bronze: reentrega da fila ou reenvio do lote pelo app.
    duplicates: int


class BronzeIngestService:
    def __init__(self, writer: BronzeWriter) -> None:
        self._writer = writer

    def ingest(self, batch: TelemetryBatchV1) -> BronzeIngestResult:
        """Grava os registros do lote que ainda não estão no bronze.

        É idempotente: a reentrega da mensagem e o reenvio do lote pelo app só
        completam o que faltava. O ``ingested_at`` vem do relógio do servidor do
        bronze, lido a cada tentativa: um carimbo tirado antes de uma espera cairia
        atrás de um cursor que a silver já passou. Levanta ``BronzeUnavailable``
        quando o bronze não responde.
        """
        ingested_at = self._writer.server_time()
        entries = [bronze_entry(batch, record, ingested_at) for record in batch.records]
        duplicates = self._writer.insert_new(entries)

        result = BronzeIngestResult(
            inserted=len(entries) - len(duplicates), duplicates=len(duplicates)
        )
        logger.info(
            "Lote %s gravado no bronze: %d novo(s), %d já estava(m) lá.",
            batch.message_id,
            result.inserted,
            result.duplicates,
        )
        return result
