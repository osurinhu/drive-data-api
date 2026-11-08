"""O layout do documento do bronze: o único lugar que o conhece.

Um documento por registro aceito, com o ``_id`` igual ao ``id`` que o app deu ao
registro: é o índice único do ``_id`` que torna a gravação idempotente. O bruto
fica em ``raw``, exatamente como chegou; os metadados da ingestão, em
``ingestion``. Os ids vão como texto (o UUID canônico), legíveis no shell e na API
da silver sem conversão. As datas vão como data BSON, que guarda milissegundos.
"""

from typing import Any

from domain.ports import BronzeEntry

#: A coleção dos registros do bronze.
RECORDS = "telemetry_records"


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
