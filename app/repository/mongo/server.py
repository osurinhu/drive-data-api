"""O que todo adaptador do bronze faz igual: o prazo de cada operação, a falha que é
da infraestrutura e a hora do servidor.

O writer (consumer) e o reader (API da silver) classificam a falha do mesmo jeito: a
que atingiria qualquer operação vira ``BronzeUnavailable`` (a porta: transitório), e
qualquer outro erro do Mongo sobe como está. Não importa ``ext``: quem chama passa o
banco.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pymongo
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import BulkWriteError, ConnectionFailure, OperationFailure, PyMongoError
from pymongo.write_concern import WriteConcern

from domain.ports import BronzeUnavailable

#: Recusas que vêm da configuração, e não da operação: a permissão (13,
#: ``Unauthorized``) e a credencial (18, ``AuthenticationFailed``). Atingem toda
#: operação, como o servidor fora do ar.
REFUSED_BY_CONFIGURATION = frozenset({13, 18})

#: O código de erro do Mongo para chave duplicada.
DUPLICATE_KEY = 11000

#: Confirmada pela maioria do replica set e no journal: o que o consumer confirmou ao
#: broker não some numa troca de primário. Num nó só (o compose), vale como a
#: confirmação do próprio nó.
DURABLE = WriteConcern(w="majority", j=True)


@contextmanager
def operation(timeout: float) -> Iterator[None]:
    """Roda o bloco com o prazo de ``timeout`` segundos e traduz a falha da
    infraestrutura em ``BronzeUnavailable``.

    O prazo vale para o bloco inteiro, inclusive a espera por um servidor: dentro
    dele, o ``serverSelectionTimeoutMS`` do cliente não vale. Sem prazo, um servidor
    que aceita a conexão e não responde prenderia quem chama.
    """
    try:
        with pymongo.timeout(timeout):
            yield
    except PyMongoError as error:
        if is_unavailable(error):
            raise BronzeUnavailable(describe(error)) from error
        raise


def insert_skipping_duplicates(
    collection: Collection[Any], documents: Sequence[dict[str, Any]], *, timeout: float
) -> set[int]:
    """Grava todos os que puder e devolve a posição, na lista, dos recusados por chave
    duplicada: o que já existe fica como está.

    Vale o write concern da coleção recebida. Sem a confirmação dele, levanta
    ``BronzeUnavailable``: gravou, e repetir é seguro. Qualquer outro erro de escrita
    sobe classificado pelo ``operation``.
    """
    with operation(timeout):
        try:
            collection.insert_many(documents, ordered=False)
        except BulkWriteError as error:
            if error.details.get("writeConcernErrors"):
                raise BronzeUnavailable("writeConcernError") from error
            write_errors = error.details.get("writeErrors", [])
            if any(write_error.get("code") != DUPLICATE_KEY for write_error in write_errors):
                raise
            return {write_error["index"] for write_error in write_errors}
    return set()


def server_time(database: Database[Any], *, timeout: float) -> datetime:
    """A hora do servidor (``hello.localTime``), com fuso."""
    with operation(timeout):
        # O `command` não herda as opções do banco: sem o `codec_options`, a data
        # voltaria sem fuso, ao contrário das que o cliente lê (tz_aware).
        hello = database.command("hello", codec_options=database.codec_options)
    local_time: datetime = hello["localTime"]
    return local_time


def is_unavailable(error: PyMongoError) -> bool:
    """A falha é da infraestrutura e atingiria qualquer operação: repetir mais tarde
    é o certo. ``timeout`` é verdadeiro nos erros de prazo do PyMongo, inclusive o do
    ``pymongo.timeout``."""
    if isinstance(error, ConnectionFailure) or error.timeout:
        return True
    return isinstance(error, OperationFailure) and error.code in REFUSED_BY_CONFIGURATION


def describe(error: PyMongoError) -> str:
    """O tipo e o código, para o log: a mensagem do PyMongo pode trazer a topologia
    inteira."""
    code = error.code if isinstance(error, OperationFailure) else None
    return type(error).__name__ if code is None else f"{type(error).__name__} {code}"
