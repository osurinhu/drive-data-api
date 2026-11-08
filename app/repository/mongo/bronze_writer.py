"""O bronze do lado de quem grava (consumer): o adaptador do ``BronzeWriter``."""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from domain.ports import BronzeEntry
from ext.mongo import mongo
from repository.mongo.bronze_document import RECORDS, SENDER_PROJECTION, sender_of, to_document
from repository.mongo.server import (
    DURABLE,
    insert_skipping_duplicates,
    operation,
    server_time,
)


class MongoBronzeWriter:
    """Grava pelo ``_id`` (o id do registro): o que já existe fica como está.

    A falha da infraestrutura vira ``BronzeUnavailable``, e o lote volta para a fila
    sem contar como falha dele: o servidor fora, em eleição ou sem resposta no prazo,
    a credencial ou a permissão recusadas (``repository.mongo.server``) e a escrita
    sem a confirmação da maioria. Qualquer outro erro do Mongo sobe como está: é do
    lote ou do código, e quem chama o trata como falha inesperada.

    ``timeout`` é o prazo de cada operação, em segundos. Sem ele, um servidor que
    aceita a conexão e não responde prenderia o consumer até o broker derrubá-lo
    por falta de heartbeat.
    """

    def __init__(self, *, timeout: float) -> None:
        self._timeout = timeout

    def server_time(self) -> datetime:
        return server_time(mongo.db, timeout=self._timeout)

    def insert_new(self, entries: Sequence[BronzeEntry]) -> set[UUID]:
        if not entries:
            return set()
        collection = mongo.db.get_collection(RECORDS, write_concern=DURABLE)
        refused = insert_skipping_duplicates(
            collection, [to_document(entry) for entry in entries], timeout=self._timeout
        )
        return {entries[position].record_id for position in refused}

    def senders_of(self, record_ids: Collection[UUID]) -> dict[UUID, int]:
        if not record_ids:
            return {}
        query = {"_id": {"$in": [str(record_id) for record_id in record_ids]}}
        with operation(self._timeout):
            documents = list(mongo.db[RECORDS].find(query, SENDER_PROJECTION))
        return dict(sender_of(document) for document in documents)
