"""Os índices do bronze, declarados num lugar só e aplicados pelo ``flask mongo setup``.

``create_index`` é idempotente: o índice que já existe com a mesma definição fica
como está. Mudar a definição de um índice existente reprova a aplicação (o Mongo
recusa dois índices com o mesmo nome): troque o nome, ou apague o antigo antes.

A exceção é a retenção, que muda sem recriar nada: o prazo de um índice TTL se ajusta
com ``collMod``. Encurtar o prazo apaga, na próxima passada do Mongo, o que ficou
velho demais. Zero dias desliga a retenção: o índice sai, porque um TTL de zero
segundos faria o contrário e apagaria cada documento assim que gravado.
"""

from typing import Any

from pymongo import ASCENDING
from pymongo.collection import Collection
from pymongo.database import Database

from repository.mongo.bronze_document import RECORDS, REJECTIONS

#: Varredura por cursor ``(ingested_at, _id)``: a ordem em que a silver lê o bronze.
INGESTED = "ix_ingested"
#: A mesma ordem, dentro de um VIN e dentro de uma métrica disponível.
VIN_INGESTED = "ix_vin_ingested"
METRIC_INGESTED = "ix_metric_ingested"
#: Para achar as recusas de uma mensagem.
REJECTION_MESSAGE = "ix_message_id"
#: A retenção do bronze. É um índice à parte porque o TTL só vale em índice de um campo.
RECORD_TTL = "ttl_ingested"
#: A retenção das recusas: é rastro de auditoria, e não dado de negócio.
REJECTION_TTL = "ttl_rejected"

_SECONDS_PER_DAY = 24 * 60 * 60


def apply(
    database: Database[dict[str, Any]],
    *,
    bronze_retention_days: int,
    rejections_retention_days: int,
) -> list[str]:
    """Cria, ou mantém como estão, os índices do bronze, e ajusta a retenção. Devolve os
    nomes dos índices aplicados. Levanta ``ValueError`` com um prazo negativo."""
    if bronze_retention_days < 0 or rejections_retention_days < 0:
        raise ValueError("a retenção em dias não pode ser negativa (0 desliga)")

    order = [("ingestion.ingested_at", ASCENDING), ("_id", ASCENDING)]
    records = database[RECORDS]
    records.create_index(order, name=INGESTED)
    records.create_index([("ingestion.vin", ASCENDING), *order], name=VIN_INGESTED)
    records.create_index([("ingestion.available_metrics", ASCENDING), *order], name=METRIC_INGESTED)

    rejections = database[REJECTIONS]
    rejections.create_index([("message_id", ASCENDING)], name=REJECTION_MESSAGE)
    applied = [INGESTED, VIN_INGESTED, METRIC_INGESTED, REJECTION_MESSAGE]
    if _apply_ttl(records, RECORD_TTL, "ingestion.ingested_at", bronze_retention_days):
        applied.append(RECORD_TTL)
    if _apply_ttl(rejections, REJECTION_TTL, "rejected_at", rejections_retention_days):
        applied.append(REJECTION_TTL)
    return applied


def _apply_ttl(collection: Collection[dict[str, Any]], name: str, field: str, days: int) -> bool:
    """Deixa o índice TTL com o prazo pedido. Devolve se ele ficou existindo."""
    current = collection.index_information().get(name)
    if days == 0:
        if current is not None:
            collection.drop_index(name)
        return False

    seconds = days * _SECONDS_PER_DAY
    if current is None:
        collection.create_index([(field, ASCENDING)], name=name, expireAfterSeconds=seconds)
    elif current.get("expireAfterSeconds") != seconds:
        collection.database.command(
            "collMod", collection.name, index={"name": name, "expireAfterSeconds": seconds}
        )
    return True
