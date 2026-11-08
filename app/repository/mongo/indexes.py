"""Os índices do bronze, declarados num lugar só e aplicados pelo ``flask mongo setup``.

``create_index`` é idempotente: o índice que já existe com a mesma definição fica
como está. Mudar a definição de um índice existente reprova a aplicação (o Mongo
recusa dois índices com o mesmo nome): troque o nome, ou apague o antigo antes.
"""

from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

from repository.mongo.bronze_document import RECORDS

#: Varredura por cursor ``(ingested_at, _id)``: a ordem em que a silver lê o bronze.
INGESTED = "ix_ingested"


def apply(database: Database[dict[str, Any]]) -> list[str]:
    """Cria, ou mantém como estão, os índices do bronze. Devolve os nomes."""
    records = database[RECORDS]
    records.create_index([("ingestion.ingested_at", ASCENDING), ("_id", ASCENDING)], name=INGESTED)
    return [INGESTED]
