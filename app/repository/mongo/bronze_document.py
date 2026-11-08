"""O layout do documento do bronze: o único lugar que o conhece.

Um documento por registro aceito, com o ``_id`` igual ao ``id`` que o app deu ao
registro: é o índice único do ``_id`` que torna a gravação idempotente. O bruto
fica em ``raw``, exatamente como chegou; os metadados da ingestão, em
``ingestion``. Os ids vão como texto (o UUID canônico), legíveis no shell e na API
da silver sem conversão. As datas vão como data BSON, que guarda milissegundos.

O writer grava com ``to_document`` e o reader lê com ``from_document``: a ida e a
volta ficam lado a lado, e ``tests/unit/test_leitura_bronze.py`` confere que uma
desfaz a outra.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from domain.ports import BronzeEntry, BronzeRejection

#: A coleção dos registros do bronze.
RECORDS = "telemetry_records"
#: A coleção do que o consumer recusou.
REJECTIONS = "telemetry_rejections"
#: O necessário de um registro para saber quem o enviou (``sender_of``).
SENDER_PROJECTION = {"ingestion.user_id": 1}


def to_document(entry: BronzeEntry) -> dict[str, Any]:
    record_id = str(entry.record_id)
    return {
        "_id": record_id,
        "raw": entry.raw,
        "ingestion": {
            "record_id": record_id,
            "vin": entry.vin,
            "vehicle_id": entry.vehicle_id,
            "user_id": entry.user_id,
            "sender_relation": entry.sender_relation,
            "payload_user_matches_sender": entry.payload_user_matches_sender,
            "collected_at": entry.collected_at,
            "available_metrics": list(entry.available_metrics),
            "received_at": entry.received_at,
            "ingested_at": entry.ingested_at,
            "message_id": str(entry.message_id),
            "request_id": entry.request_id,
            "schema_version": entry.schema_version,
        },
    }


def sender_of(document: Mapping[str, Any]) -> tuple[UUID, int]:
    return UUID(document["_id"]), document["ingestion"]["user_id"]


def to_rejection_document(rejection: BronzeRejection) -> dict[str, Any]:
    """O ``_id`` junta a mensagem e o registro: a reentrega da mensagem cai no mesmo
    documento, e o reenvio do lote pelo app, que é outra mensagem, gera outro."""
    return {
        "_id": f"{rejection.message_id}:{rejection.record_id}",
        "record_id": str(rejection.record_id),
        "vin": rejection.vin,
        "user_id": rejection.user_id,
        "reason": rejection.reason.value,
        "message_id": str(rejection.message_id),
        "request_id": rejection.request_id,
        "received_at": rejection.received_at,
        "rejected_at": rejection.rejected_at,
        "schema_version": rejection.schema_version,
        "raw": rejection.raw,
    }


def from_document(document: Mapping[str, Any]) -> BronzeEntry:
    ingestion = document["ingestion"]
    return BronzeEntry(
        record_id=UUID(document["_id"]),
        vin=ingestion["vin"],
        vehicle_id=ingestion["vehicle_id"],
        user_id=ingestion["user_id"],
        sender_relation=ingestion["sender_relation"],
        payload_user_matches_sender=ingestion["payload_user_matches_sender"],
        collected_at=ingestion["collected_at"],
        available_metrics=tuple(ingestion["available_metrics"]),
        received_at=ingestion["received_at"],
        ingested_at=ingestion["ingested_at"],
        message_id=UUID(ingestion["message_id"]),
        request_id=ingestion["request_id"],
        schema_version=ingestion["schema_version"],
        raw=document["raw"],
    )
