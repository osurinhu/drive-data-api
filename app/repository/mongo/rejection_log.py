"""O registro do que o consumer recusou: o adaptador do ``RejectionLog``."""

from collections.abc import Sequence

from domain.ports import BronzeRejection
from ext.mongo import mongo
from repository.mongo.bronze_document import REJECTIONS, to_rejection_document
from repository.mongo.server import DURABLE, insert_skipping_duplicates


class MongoRejectionLog:
    """Grava cada recusa uma vez só: a reentrega da mensagem traz o mesmo ``_id``.

    Com a mesma durabilidade do bronze, porque a mensagem recebe ack depois daqui: a
    recusa perdida não teria outro rastro. A falha da infraestrutura vira
    ``BronzeUnavailable``, e a mensagem volta para a fila, como na gravação.
    """

    def __init__(self, *, timeout: float) -> None:
        self._timeout = timeout

    def record(self, rejections: Sequence[BronzeRejection]) -> None:
        if not rejections:
            return
        collection = mongo.db.get_collection(REJECTIONS, write_concern=DURABLE)
        documents = [to_rejection_document(rejection) for rejection in rejections]
        insert_skipping_duplicates(collection, documents, timeout=self._timeout)
