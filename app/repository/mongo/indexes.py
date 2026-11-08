"""Os índices do bronze, declarados num lugar só e aplicados pelo ``flask mongo setup``.

``create_index`` é idempotente: o índice que já existe com a mesma definição fica
como está. Mudar a definição de um índice existente reprova a aplicação (o Mongo
recusa dois índices com o mesmo nome): troque o nome, ou apague o antigo antes.
"""

from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

from repository.mongo.bronze_document import RECORDS, REJECTIONS

#: Varredura por cursor ``(ingested_at, _id)``: a ordem em que a silver lê o bronze.
INGESTED = "ix_ingested"
#: A mesma ordem, dentro de um VIN e dentro de uma métrica disponível.
VIN_INGESTED = "ix_vin_ingested"
METRIC_INGESTED = "ix_metric_ingested"
#: Para achar as recusas de uma mensagem.
REJECTION_MESSAGE = "ix_message_id"
#: A retenção das recusas: é rastro de auditoria, e não dado de negócio.
REJECTION_TTL = "ttl_rejected"
REJECTION_TTL_SECONDS = 30 * 24 * 60 * 60


def apply(database: Database[dict[str, Any]]) -> list[str]:
    """Cria, ou mantém como estão, os índices do bronze. Devolve os nomes."""
    order = [("ingestion.ingested_at", ASCENDING), ("_id", ASCENDING)]
    records = database[RECORDS]
    records.create_index(order, name=INGESTED)
    records.create_index([("ingestion.vin", ASCENDING), *order], name=VIN_INGESTED)
    records.create_index([("ingestion.available_metrics", ASCENDING), *order], name=METRIC_INGESTED)

    rejections = database[REJECTIONS]
    rejections.create_index([("message_id", ASCENDING)], name=REJECTION_MESSAGE)
    rejections.create_index(
        [("rejected_at", ASCENDING)], name=REJECTION_TTL, expireAfterSeconds=REJECTION_TTL_SECONDS
    )
    return [INGESTED, VIN_INGESTED, METRIC_INGESTED, REJECTION_MESSAGE, REJECTION_TTL]
