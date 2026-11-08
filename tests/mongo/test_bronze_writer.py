"""O adaptador do bronze (``MongoBronzeWriter``) e os índices contra o Mongo de verdade.

O contexto de aplicação que o adaptador exige (``mongo.db``) vem do conftest desta
pasta.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from domain.ports import BronzeEntry, BronzeUnavailable
from repository.mongo import indexes
from repository.mongo.bronze_document import RECORDS, REJECTIONS
from repository.mongo.bronze_writer import MongoBronzeWriter

#: Milissegundos: a precisão da data BSON.
COLETADO = datetime(2026, 11, 7, 14, 3, 11, 482000, tzinfo=UTC)


def _registro(**campos) -> BronzeEntry:
    record_id = uuid4()
    valores = {
        "record_id": record_id,
        "vin": "93YBSR7RHEJ298116",
        "vehicle_id": 7,
        "user_id": 51,
        "sender_relation": "driver",
        "payload_user_matches_sender": False,
        "collected_at": COLETADO,
        "available_metrics": ("rpm", "speed"),
        "received_at": COLETADO + timedelta(seconds=1),
        "ingested_at": COLETADO + timedelta(seconds=2),
        "message_id": uuid4(),
        "request_id": "req-1",
        "schema_version": 1,
        "raw": {"id": str(record_id), "user": "admin", "readings": [{"metric": "rpm"}]},
    }
    return BronzeEntry(**{**valores, **campos})


@pytest.fixture
def writer():
    return MongoBronzeWriter(timeout=5)


def test_grava_um_documento_por_registro_no_layout_do_bronze(writer, mongo_db):
    registro = _registro()

    duplicados = writer.insert_new([registro])

    assert duplicados == set()
    documento = mongo_db[RECORDS].find_one({"_id": str(registro.record_id)})
    assert documento == {
        "_id": str(registro.record_id),
        "raw": registro.raw,
        "ingestion": {
            "record_id": str(registro.record_id),
            "vin": "93YBSR7RHEJ298116",
            "vehicle_id": 7,
            "user_id": 51,
            "sender_relation": "driver",
            "payload_user_matches_sender": False,
            "collected_at": COLETADO,
            "available_metrics": ["rpm", "speed"],
            "received_at": registro.received_at,
            "ingested_at": registro.ingested_at,
            "message_id": str(registro.message_id),
            "request_id": "req-1",
            "schema_version": 1,
        },
    }


def test_regravar_devolve_os_que_ja_estavam_e_nao_os_altera(writer, mongo_db):
    registros = [_registro(), _registro()]
    writer.insert_new(registros)
    reentrega = [_registro(record_id=r.record_id, request_id="outra") for r in registros]

    duplicados = writer.insert_new(reentrega)

    assert duplicados == {r.record_id for r in registros}
    assert mongo_db[RECORDS].count_documents({}) == 2
    assert mongo_db[RECORDS].count_documents({"ingestion.request_id": "req-1"}) == 2


def test_lote_gravado_em_parte_so_completa_o_que_faltava(writer, mongo_db):
    """A reentrega de um lote que caiu no meio da gravação."""
    ja_gravado, novo = _registro(), _registro()
    writer.insert_new([ja_gravado])

    duplicados = writer.insert_new([ja_gravado, novo])

    assert duplicados == {ja_gravado.record_id}
    assert mongo_db[RECORDS].count_documents({}) == 2


def test_diz_quem_enviou_cada_registro_gravado(writer):
    meu, alheio, ausente = _registro(user_id=51), _registro(user_id=52), _registro()
    writer.insert_new([meu, alheio])

    remetentes = writer.senders_of({meu.record_id, alheio.record_id, ausente.record_id})

    assert remetentes == {meu.record_id: 51, alheio.record_id: 52}


def test_sem_ids_nao_consulta_nada(writer):
    assert writer.senders_of(set()) == {}


def test_a_hora_do_servidor_vem_com_fuso_e_e_a_de_agora(writer):
    hora = writer.server_time()

    assert hora.tzinfo is not None
    assert abs(hora - datetime.now(UTC)) < timedelta(minutes=1)


def test_mongo_fora_do_ar_vira_bronze_indisponivel(mongo_avulso):
    # Dentro do prazo da operação, a espera pelo servidor é o próprio prazo (o
    # MONGO_TIMEOUT_SECONDS não vale ali): um prazo curto deixa o teste rápido.
    writer = MongoBronzeWriter(timeout=0.5)

    with mongo_avulso("mongo-fora-do-ar", "mongodb://ninguem:nada@127.0.0.1:1/"):
        with pytest.raises(BronzeUnavailable):
            writer.server_time()
        with pytest.raises(BronzeUnavailable):
            writer.insert_new([_registro()])


def test_credencial_recusada_vira_bronze_indisponivel(_app_do_mongo, mongo_avulso):
    """Credencial ou permissão erradas atingem toda mensagem, como o Mongo fora do ar:
    o lote volta para a fila, em vez de gastar o limite de entregas e ir para a DLQ.

    A primeira operação recebe a recusa (``OperationFailure`` 18). Depois dela, o
    PyMongo marca o servidor como desconhecido, e a seguinte esgota o prazo procurando
    um servidor (``ServerSelectionTimeoutError``): as duas são transitórias."""
    writer = MongoBronzeWriter(timeout=0.5)
    uri = urlsplit(_app_do_mongo.config["MONGO_URI"])
    netloc = f"{uri.username}:senha-errada@{uri.hostname}:{uri.port or 27017}"

    with mongo_avulso("credencial-errada", urlunsplit(uri._replace(netloc=netloc))):
        with pytest.raises(BronzeUnavailable, match="OperationFailure 18"):
            writer.server_time()
        with pytest.raises(BronzeUnavailable):
            writer.insert_new([_registro()])


def test_operacao_fora_do_prazo_vira_bronze_indisponivel(mongo_db):
    """O servidor que aceita a conexão e não responde no prazo é transitório. O prazo
    minúsculo estoura antes de a operação sair, e nada é gravado."""
    sem_tempo = MongoBronzeWriter(timeout=0.000001)

    with pytest.raises(BronzeUnavailable):
        sem_tempo.server_time()
    with pytest.raises(BronzeUnavailable):
        sem_tempo.insert_new([_registro()])

    assert mongo_db[RECORDS].count_documents({}) == 0


def test_os_indices_do_bronze_se_aplicam_de_novo_sem_mudar_nada(mongo_db):
    """O setup roda a cada `docker compose up`."""
    indexes.apply(mongo_db, bronze_retention_days=365, rejections_retention_days=30)

    nomes = indexes.apply(mongo_db, bronze_retention_days=365, rejections_retention_days=30)

    existentes = mongo_db[RECORDS].index_information()
    ordem = [("ingestion.ingested_at", 1), ("_id", 1)]
    assert existentes[indexes.INGESTED]["key"] == ordem
    assert existentes[indexes.VIN_INGESTED]["key"] == [("ingestion.vin", 1), *ordem]
    assert existentes[indexes.METRIC_INGESTED]["key"] == [
        ("ingestion.available_metrics", 1),
        *ordem,
    ]
    recusas = mongo_db[REJECTIONS].index_information()
    assert set(nomes) <= set(existentes) | set(recusas)
    assert recusas[indexes.REJECTION_MESSAGE]["key"] == [("message_id", 1)]
    assert recusas[indexes.REJECTION_TTL]["key"] == [("rejected_at", 1)]
    assert recusas[indexes.REJECTION_TTL]["expireAfterSeconds"] == 30 * 24 * 60 * 60
