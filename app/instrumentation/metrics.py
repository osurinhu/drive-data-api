"""As métricas do Prometheus e os adaptadores das portas de observação.

Os serviços e o consumer só conhecem as portas (``IngestObserver``, ``BronzeObserver``,
``BronzeReadObserver``, ``ConsumerObserver``). É aqui que cada acontecimento vira
métrica e linha de log com ``event``. Os instrumentos são globais do processo, e cada
adaptador é só um jeito de chegar a eles: não guarda estado.

No gunicorn, cada worker é um processo. O ``docker/gunicorn.conf.py`` define
``PROMETHEUS_MULTIPROC_DIR`` antes de os workers importarem esta biblioteca, e o
``render`` soma os arquivos de todos eles. Sem a variável (o consumer, os testes),
vale o registro do próprio processo.
"""

import logging
import os
from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.multiprocess import MultiProcessCollector

from domain.contracts.telemetry_batch import TelemetryBatchV1
from instrumentation.correlation import bind_correlation, clear_correlation

logger = logging.getLogger(__name__)

_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
_STAMP_BUCKETS = (0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 15, 30, 60)

# --- HTTP ---------------------------------------------------------------------------
HTTP_REQUESTS = Counter("http_requests_total", "Requisições HTTP.", ["route", "status"])
HTTP_SECONDS = Histogram("http_request_seconds", "Duração das requisições HTTP.", ["route"])

# --- api ----------------------------------------------------------------------------
INGEST_RECORDS = Counter(
    "ingest_records_total", "Registros recebidos do app.", ["outcome", "reason"]
)
INGEST_BATCHES = Counter("ingest_batches_total", "Lotes recebidos do app.", ["outcome"])
INGEST_PUBLISH_SECONDS = Histogram("ingest_publish_seconds", "Publicação com confirmação.")
PAYLOAD_USER_MISMATCH = Counter(
    "payload_user_mismatch_total", "Registros cujo `user` difere do remetente do token."
)

# --- consumer -----------------------------------------------------------------------
CONSUMER_MESSAGES = Counter("consumer_messages_total", "Destino das mensagens.", ["outcome"])
BRONZE_RECORDS = Counter("bronze_records_total", "Registros tratados no bronze.", ["outcome"])
CONSUMER_BATCH_SECONDS = Histogram("consumer_batch_seconds", "Da entrega à gravação do lote.")
PIPELINE_LATENCY = Histogram(
    "pipeline_latency_seconds", "Do recebimento à gravação.", buckets=_LATENCY_BUCKETS
)
STAMP_AGE = Histogram(
    "bronze_stamp_age_seconds", "Do carimbo à gravação confirmada.", buckets=_STAMP_BUCKETS
)
CONSUMER_READY = Gauge("consumer_ready", "1 se o consumer está pronto.")

# --- bronze-api ---------------------------------------------------------------------
READ_RECORDS = Counter("bronze_read_records_total", "Registros entregues à silver.")
SILVER_LAG = Gauge(
    "bronze_silver_lag_seconds",
    "Atraso da silver: watermark menos a posição, na leitura sem filtro.",
    multiprocess_mode="livemostrecent",
)


def render() -> tuple[bytes, str]:
    """O corpo e o tipo da resposta do ``/metrics``."""
    registry = REGISTRY
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        MultiProcessCollector(registry)
    return generate_latest(registry), CONTENT_TYPE_LATEST


def observe_http(route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(route=route, status=str(status)).inc()
    HTTP_SECONDS.labels(route=route).observe(seconds)


class IngestMetrics:
    """``IngestObserver``."""

    def record_rejected(self, *, record_id: UUID, vin: str, reason: str) -> None:
        INGEST_RECORDS.labels(outcome="rejected", reason=reason).inc()
        logger.info(
            "Registro %s não publicado: %s.",
            record_id,
            reason,
            extra={
                "event": "telemetry.record.rejected",
                "record_id": record_id,
                "vin": vin,
                "reason": reason,
            },
        )

    def nothing_accepted(self) -> None:
        INGEST_BATCHES.labels(outcome="nothing_accepted").inc()

    def batch_published(self, batch: TelemetryBatchV1, *, publish_seconds: float) -> None:
        accepted = len(batch.records)
        INGEST_BATCHES.labels(outcome="published").inc()
        INGEST_RECORDS.labels(outcome="accepted", reason="").inc(accepted)
        INGEST_PUBLISH_SECONDS.observe(publish_seconds)
        mismatches = sum(not record.payload_user_matches_sender for record in batch.records)
        PAYLOAD_USER_MISMATCH.inc(mismatches)
        logger.info(
            "Lote %s publicado: %d registro(s).",
            batch.message_id,
            accepted,
            extra={
                "event": "telemetry.batch.published",
                "message_id": batch.message_id,
                "accepted": accepted,
                "publish_ms": round(publish_seconds * 1000, 1),
            },
        )

    def publish_failed(self, batch: TelemetryBatchV1, *, reason: str) -> None:
        INGEST_BATCHES.labels(outcome="publish_failed").inc()
        logger.warning(
            "Lote %s não publicado: %s.",
            batch.message_id,
            reason,
            extra={
                "event": "telemetry.publish.failed",
                "message_id": batch.message_id,
                "reason": reason,
            },
        )


class BronzeMetrics:
    """``BronzeObserver``."""

    def batch_ingested(
        self,
        batch: TelemetryBatchV1,
        *,
        ingested_at: datetime,
        stamp_age_seconds: float,
        inserted: int,
        duplicates: Collection[UUID],
        collisions: Collection[UUID],
        invalid: Collection[UUID],
    ) -> None:
        BRONZE_RECORDS.labels(outcome="inserted").inc(inserted)
        BRONZE_RECORDS.labels(outcome="duplicate").inc(len(duplicates))
        BRONZE_RECORDS.labels(outcome="collision").inc(len(collisions))
        BRONZE_RECORDS.labels(outcome="invalid").inc(len(invalid))
        PIPELINE_LATENCY.observe((ingested_at - batch.received_at).total_seconds())
        STAMP_AGE.observe(stamp_age_seconds)
        logger.info(
            "Lote %s gravado no bronze: %d novo(s), %d já estava(m) lá.",
            batch.message_id,
            inserted,
            len(duplicates),
            extra={
                "event": "bronze.batch.ingested",
                "inserted": inserted,
                "duplicates": len(duplicates),
                "collisions": len(collisions),
                "invalid": len(invalid),
            },
        )
        # Reenvio do app é rotina, e um lote tem até 500 registros: só em DEBUG.
        for record_id in duplicates:
            logger.debug(
                "Registro %s já estava no bronze.",
                record_id,
                extra={"event": "bronze.record.duplicate", "record_id": record_id},
            )
        for record_id in collisions:
            logger.warning(
                "Registro %s recusado: o id já é de outro remetente.",
                record_id,
                extra={"event": "bronze.record.collision", "record_id": record_id},
            )
        for record_id in invalid:
            logger.warning(
                "Registro %s recusado: VIN fora da forma.",
                record_id,
                extra={"event": "bronze.record.invalid", "record_id": record_id},
            )


class BronzeReadMetrics:
    """``BronzeReadObserver``."""

    def page_read(self, *, records: int, lag_seconds: float | None) -> None:
        READ_RECORDS.inc(records)
        if lag_seconds is not None:
            SILVER_LAG.set(lag_seconds)


class ConsumerMetrics:
    """``ConsumerObserver``: a correlação de cada mensagem e o destino dela."""

    def message_started(
        self, *, message_id: str | None, request_id: str | None, user_id: int | None
    ) -> None:
        bind_correlation(message_id=message_id, request_id=request_id, user_id=user_id)

    def message_finished(self) -> None:
        clear_correlation()

    def acked(self, *, seconds: float) -> None:
        CONSUMER_MESSAGES.labels(outcome="acked").inc()
        CONSUMER_BATCH_SECONDS.observe(seconds)

    def dead_lettered(self, *, reason: str) -> None:
        CONSUMER_MESSAGES.labels(outcome="dead_lettered").inc()
        logger.warning(
            "Mensagem fora do contrato, enviada à DLQ: %.300s",
            reason,
            extra={"event": "bronze.message.dead_lettered"},
        )

    def requeued(self, *, reason: str) -> None:
        CONSUMER_MESSAGES.labels(outcome="requeued").inc()
        logger.info("Mensagem de volta à fila: %s.", reason, extra={"reason": reason})

    def paused(self) -> None:
        logger.warning(
            "Consumo em pausa: o bronze não responde.", extra={"event": "consumer.paused"}
        )

    def resumed(self) -> None:
        logger.info("Consumo retomado: o bronze voltou.", extra={"event": "consumer.resumed"})
