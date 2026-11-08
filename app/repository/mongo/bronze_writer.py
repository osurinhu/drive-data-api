"""O bronze do lado de quem grava (consumer): o adaptador do ``BronzeWriter``."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import pymongo
from pymongo.errors import BulkWriteError, ConnectionFailure, OperationFailure, PyMongoError
from pymongo.write_concern import WriteConcern

from domain.ports import BronzeEntry, BronzeUnavailable
from ext.mongo import mongo
from repository.mongo.bronze_document import RECORDS, to_document

#: O código de erro do Mongo para chave duplicada.
DUPLICATE_KEY = 11000

#: Recusas que vêm da configuração, e não do lote: a permissão (13, ``Unauthorized``)
#: e a credencial (18, ``AuthenticationFailed``). Atingem toda mensagem, como o
#: servidor fora do ar.
REFUSED_BY_CONFIGURATION = frozenset({13, 18})

#: Confirmada pela maioria do replica set e no journal: o registro que o consumer
#: confirmou ao broker não some numa troca de primário. Num nó só (o compose), vale
#: como a confirmação do próprio nó.
DURABLE = WriteConcern(w="majority", j=True)


class MongoBronzeWriter:
    """Grava pelo ``_id`` (o id do registro): o que já existe fica como está.

    A falha da infraestrutura vira ``BronzeUnavailable``, e o lote volta para a fila
    sem contar como falha dele: o servidor fora, em eleição ou sem resposta no prazo
    (``ConnectionFailure``, os erros de prazo), a credencial ou a permissão recusadas
    e a escrita sem a confirmação da maioria. Qualquer outro erro do Mongo sobe como
    está: é do lote ou do código, e quem chama o trata como falha inesperada.

    ``timeout`` é o prazo de cada operação, em segundos. Sem ele, um servidor que
    aceita a conexão e não responde prenderia o consumer até o broker derrubá-lo
    por falta de heartbeat.
    """

    def __init__(self, *, timeout: float) -> None:
        self._timeout = timeout

    def server_time(self) -> datetime:
        database = mongo.db
        try:
            with pymongo.timeout(self._timeout):
                # O `command` não herda as opções do banco: sem o `codec_options`, a
                # data voltaria sem fuso, ao contrário das que o cliente lê (tz_aware).
                hello = database.command("hello", codec_options=database.codec_options)
        except PyMongoError as error:
            if _is_unavailable(error):
                raise BronzeUnavailable(_describe(error)) from error
            raise
        local_time: datetime = hello["localTime"]
        return local_time

    def insert_new(self, entries: Sequence[BronzeEntry]) -> set[UUID]:
        if not entries:
            return set()
        collection = mongo.db.get_collection(RECORDS, write_concern=DURABLE)
        try:
            with pymongo.timeout(self._timeout):
                collection.insert_many([to_document(entry) for entry in entries], ordered=False)
        except BulkWriteError as error:
            if error.details.get("writeConcernErrors"):
                # Gravou, sem a confirmação da maioria: repetir é seguro (idempotente).
                raise BronzeUnavailable("writeConcernError") from error
            duplicates = _duplicates(entries, error.details)
            if duplicates is not None:
                return duplicates
            if _is_unavailable(error):
                raise BronzeUnavailable(_describe(error)) from error
            raise
        except PyMongoError as error:
            if _is_unavailable(error):
                raise BronzeUnavailable(_describe(error)) from error
            raise
        return set()


def _is_unavailable(error: PyMongoError) -> bool:
    """A falha é da infraestrutura e atingiria qualquer lote: repetir mais tarde é o
    certo. ``timeout`` é verdadeiro nos erros de prazo do PyMongo, inclusive o do
    ``pymongo.timeout``."""
    if isinstance(error, ConnectionFailure) or error.timeout:
        return True
    return isinstance(error, OperationFailure) and error.code in REFUSED_BY_CONFIGURATION


def _describe(error: PyMongoError) -> str:
    """O tipo e o código, para o log: a mensagem do PyMongo pode trazer a topologia
    inteira."""
    code = error.code if isinstance(error, OperationFailure) else None
    return type(error).__name__ if code is None else f"{type(error).__name__} {code}"


def _duplicates(entries: Sequence[BronzeEntry], details: Mapping[str, Any]) -> set[UUID] | None:
    """Os ids recusados por chave duplicada, ou ``None`` se algum erro for de outro tipo.

    Com ``ordered=False``, o Mongo grava todos os que pode e lista os recusados pelo
    índice na lista enviada.
    """
    write_errors = details.get("writeErrors", [])
    if any(error.get("code") != DUPLICATE_KEY for error in write_errors):
        return None
    return {entries[error["index"]].record_id for error in write_errors}
