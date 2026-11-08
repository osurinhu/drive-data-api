"""O registro das recusas do consumer (``MongoRejectionLog``) contra o Mongo de verdade."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from domain.ports import BronzeRejection, BronzeRejectionReason, BronzeUnavailable
from repository.mongo.bronze_document import REJECTIONS
from repository.mongo.rejection_log import MongoRejectionLog

#: Milissegundos: a precisão da data BSON.
RECUSADO = datetime(2026, 11, 7, 14, 3, 11, 482000, tzinfo=UTC)


def _recusa(**campos) -> BronzeRejection:
    record_id = uuid4()
    valores = {
        "record_id": record_id,
        "vin": "93YBSR7RHEJ298116",
        "user_id": 52,
        "reason": BronzeRejectionReason.ID_COLLISION,
        "message_id": uuid4(),
        "request_id": "req-1",
        "received_at": RECUSADO - timedelta(seconds=1),
        "rejected_at": RECUSADO,
        "schema_version": 1,
        "raw": {"id": str(record_id), "user": "outra"},
    }
    return BronzeRejection(**{**valores, **campos})


@pytest.fixture
def log():
    return MongoRejectionLog(timeout=5)


def test_grava_o_motivo_o_envelope_e_o_bruto(log, mongo_db):
    recusa = _recusa()

    log.record([recusa])

    assert mongo_db[REJECTIONS].find_one() == {
        "_id": f"{recusa.message_id}:{recusa.record_id}",
        "record_id": str(recusa.record_id),
        "vin": "93YBSR7RHEJ298116",
        "user_id": 52,
        "reason": "id_collision",
        "message_id": str(recusa.message_id),
        "request_id": "req-1",
        "received_at": recusa.received_at,
        "rejected_at": RECUSADO,
        "schema_version": 1,
        "raw": recusa.raw,
    }


def test_a_reentrega_da_mensagem_nao_duplica_a_recusa(log, mongo_db):
    recusa, nova = _recusa(), _recusa()
    log.record([recusa])

    log.record([recusa, nova])

    assert mongo_db[REJECTIONS].count_documents({}) == 2


def test_o_reenvio_do_lote_e_outra_mensagem_e_deixa_outro_rastro(log, mongo_db):
    primeira = _recusa()
    log.record([primeira])

    log.record([_recusa(record_id=primeira.record_id)])

    assert mongo_db[REJECTIONS].count_documents({}) == 2


def test_lista_vazia_nao_grava_nada(log, mongo_db):
    log.record([])

    assert mongo_db[REJECTIONS].count_documents({}) == 0


def test_mongo_fora_do_ar_vira_bronze_indisponivel(mongo_avulso):
    fora = MongoRejectionLog(timeout=0.5)

    with (
        mongo_avulso("recusa-sem-mongo", "mongodb://ninguem:nada@127.0.0.1:1/"),
        pytest.raises(BronzeUnavailable),
    ):
        fora.record([_recusa()])
