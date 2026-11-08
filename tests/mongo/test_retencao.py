"""A retenção do bronze e das recusas: índices TTL, ajustados pelo ``flask mongo setup``."""

import pytest

from repository.mongo import indexes
from repository.mongo.bronze_document import RECORDS, REJECTIONS

UM_DIA = 24 * 60 * 60


def _aplicar(mongo_db, *, bronze: int = 365, recusas: int = 30) -> list[str]:
    return indexes.apply(mongo_db, bronze_retention_days=bronze, rejections_retention_days=recusas)


def _prazo(mongo_db, colecao: str, nome: str) -> int | None:
    indice = mongo_db[colecao].index_information().get(nome)
    return None if indice is None else indice["expireAfterSeconds"]


def test_os_prazos_nascem_em_segundos_no_campo_de_gravacao(mongo_db):
    nomes = _aplicar(mongo_db, bronze=365, recusas=30)

    registros = mongo_db[RECORDS].index_information()[indexes.RECORD_TTL]
    assert registros["key"] == [("ingestion.ingested_at", 1)]
    assert registros["expireAfterSeconds"] == 365 * UM_DIA
    assert _prazo(mongo_db, REJECTIONS, indexes.REJECTION_TTL) == 30 * UM_DIA
    assert {indexes.RECORD_TTL, indexes.REJECTION_TTL} <= set(nomes)


def test_mudar_o_prazo_ajusta_o_indice_que_ja_existe(mongo_db):
    _aplicar(mongo_db, bronze=365, recusas=30)

    _aplicar(mongo_db, bronze=90, recusas=7)

    assert _prazo(mongo_db, RECORDS, indexes.RECORD_TTL) == 90 * UM_DIA
    assert _prazo(mongo_db, REJECTIONS, indexes.REJECTION_TTL) == 7 * UM_DIA


def test_aplicar_de_novo_com_o_mesmo_prazo_nao_muda_nada(mongo_db):
    """O setup roda a cada `docker compose up`."""
    _aplicar(mongo_db)
    antes = mongo_db[RECORDS].index_information()

    _aplicar(mongo_db)

    assert mongo_db[RECORDS].index_information() == antes


def test_zero_dias_tira_o_indice_em_vez_de_expirar_na_hora(mongo_db):
    """No Mongo, ``expireAfterSeconds: 0`` apaga o documento assim que o monitor passa."""
    _aplicar(mongo_db, bronze=365)

    nomes = _aplicar(mongo_db, bronze=0)

    assert indexes.RECORD_TTL not in mongo_db[RECORDS].index_information()
    assert indexes.RECORD_TTL not in nomes


def test_zero_dias_sem_o_indice_nao_falha(mongo_db):
    _aplicar(mongo_db, bronze=0, recusas=0)

    assert indexes.RECORD_TTL not in mongo_db[RECORDS].index_information()
    assert indexes.REJECTION_TTL not in mongo_db[REJECTIONS].index_information()


def test_a_retencao_desligada_volta_quando_ganha_prazo(mongo_db):
    _aplicar(mongo_db, bronze=0)

    _aplicar(mongo_db, bronze=30)

    assert _prazo(mongo_db, RECORDS, indexes.RECORD_TTL) == 30 * UM_DIA


@pytest.mark.parametrize(("bronze", "recusas"), [(-1, 30), (365, -1)], ids=["bronze", "recusas"])
def test_prazo_negativo_e_recusado_antes_de_mexer_em_qualquer_indice(mongo_db, bronze, recusas):
    with pytest.raises(ValueError, match="negativa"):
        _aplicar(mongo_db, bronze=bronze, recusas=recusas)

    assert mongo_db.list_collection_names() == []
