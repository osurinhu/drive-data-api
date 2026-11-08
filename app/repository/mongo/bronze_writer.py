"""O bronze do lado de quem grava (consumer): o adaptador do ``BronzeWriter``."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from pymongo.errors import BulkWriteError
from pymongo.write_concern import WriteConcern

from domain.ports import BronzeEntry, BronzeUnavailable
from ext.mongo import mongo
from repository.mongo.bronze_document import RECORDS, to_document
from repository.mongo.server import operation, server_time

#: O código de erro do Mongo para chave duplicada.
DUPLICATE_KEY = 11000

#: Confirmada pela maioria do replica set e no journal: o registro que o consumer
#: confirmou ao broker não some numa troca de primário. Num nó só (o compose), vale
#: como a confirmação do próprio nó.
DURABLE = WriteConcern(w="majority", j=True)


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
        with operation(self._timeout):
            try:
                collection.insert_many([to_document(entry) for entry in entries], ordered=False)
            except BulkWriteError as error:
                if error.details.get("writeConcernErrors"):
                    # Gravou, sem a confirmação da maioria: repetir é seguro (idempotente).
                    raise BronzeUnavailable("writeConcernError") from error
                duplicates = _duplicates(entries, error.details)
                if duplicates is None:
                    # Outro erro de escrita: o `operation` o classifica.
                    raise
                return duplicates
        return set()


def _duplicates(entries: Sequence[BronzeEntry], details: Mapping[str, Any]) -> set[UUID] | None:
    """Os ids recusados por chave duplicada, ou ``None`` se algum erro for de outro tipo.

    Com ``ordered=False``, o Mongo grava todos os que pode e lista os recusados pelo
    índice na lista enviada.
    """
    write_errors = details.get("writeErrors", [])
    if any(error.get("code") != DUPLICATE_KEY for error in write_errors):
        return None
    return {entries[error["index"]].record_id for error in write_errors}
