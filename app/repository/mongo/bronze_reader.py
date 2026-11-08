"""O bronze do lado de quem lê (a API da silver): o adaptador do ``BronzeReader``."""

from datetime import datetime
from typing import Any

from pymongo import ASCENDING
from pymongo.read_concern import ReadConcern

from domain.authz import ANY_OWNER, OwnerScope
from domain.ports import BronzeEntry, BronzeFilters, BronzePosition
from ext.mongo import mongo
from repository.mongo.bronze_document import RECORDS, from_document
from repository.mongo.server import operation, server_time

#: Só o que a maioria do replica set confirmou: a silver nunca lê um registro que uma
#: troca de primário desfaria. É o par do ``DURABLE`` da gravação.
MAJORITY = ReadConcern("majority")

#: A ordem de leitura, a do índice ``ix_ingested`` (``repository.mongo.indexes``).
ORDER = [("ingestion.ingested_at", ASCENDING), ("_id", ASCENDING)]


class MongoBronzeReader:
    """Lê o bronze em ordem de gravação, a partir de uma posição.

    A falha da infraestrutura vira ``BronzeUnavailable``, como no writer
    (``repository.mongo.server``). ``timeout`` é o prazo de cada operação, em
    segundos: sem ele, um servidor que aceita a conexão e não responde prenderia o
    worker do gunicorn até o timeout dele.
    """

    def __init__(self, *, timeout: float) -> None:
        self._timeout = timeout

    def server_time(self) -> datetime:
        return server_time(mongo.db, timeout=self._timeout)

    def read_after(
        self,
        after: BronzePosition,
        *,
        until: datetime,
        limit: int,
        owner_scope: OwnerScope,
        filters: BronzeFilters,
    ) -> list[BronzeEntry]:
        if limit < 1:
            # No PyMongo, `limit=0` é "sem limite": a página viria com o bronze inteiro.
            raise ValueError("limit precisa ser positivo.")
        query = page_query(after, until=until, owner_scope=owner_scope, filters=filters)
        collection = mongo.db.get_collection(RECORDS, read_concern=MAJORITY)
        with operation(self._timeout):
            documents = list(collection.find(query, sort=ORDER, limit=limit))
        return [from_document(document) for document in documents]


def page_query(
    after: BronzePosition, *, until: datetime, owner_scope: OwnerScope, filters: BronzeFilters
) -> dict[str, Any]:
    """O filtro da página: depois de ``after`` na ordem ``(ingested_at, _id)``, até
    ``until``, dentro de ``filters`` e, com um id em ``owner_scope``, só do que esse
    usuário enviou.

    O intervalo fica todo no primeiro campo do índice e vira o limite da varredura;
    o ``$or`` só descarta, no próprio índice, o que empata com a posição no
    milissegundo sem passar dela no ``_id``. O VIN e a métrica têm índice próprio, com a
    mesma ordem depois deles. O período de coleta não tem: descarta o que a varredura
    trouxe, e um recorte grande deve vir com um VIN ou um ``ingested_from``. O
    ``tests/mongo/test_bronze_reader.py`` confere pelo ``explain`` que nenhuma dessas
    páginas ordena em memória.
    """
    query: dict[str, Any] = {
        "ingestion.ingested_at": {"$gte": after.ingested_at, "$lte": until},
        "$or": [
            {"ingestion.ingested_at": {"$gt": after.ingested_at}},
            {"_id": {"$gt": str(after.record_id)}},
        ],
    }
    if owner_scope is not ANY_OWNER:
        query["ingestion.user_id"] = owner_scope
    if filters.vin is not None:
        query["ingestion.vin"] = filters.vin
    if filters.metrics:
        query["ingestion.available_metrics"] = {"$in": list(filters.metrics)}
    collected: dict[str, datetime] = {}
    if filters.collected_from is not None:
        collected["$gte"] = filters.collected_from
    if filters.collected_to is not None:
        collected["$lte"] = filters.collected_to
    if collected:
        query["ingestion.collected_at"] = collected
    return query
