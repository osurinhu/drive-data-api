"""O cliente do Mongo contra o servidor de verdade."""

from datetime import UTC, datetime


def test_a_suite_grava_e_le_no_banco_de_testes(mongo_db):
    """A credencial da suíte grava, e as datas voltam com fuso (``tz_aware``),
    comparáveis às do Postgres sem conversão."""
    coletado = datetime(2026, 11, 2, 14, 3, 11, 482000, tzinfo=UTC)
    mongo_db["sonda"].insert_one({"_id": "r1", "collected_at": coletado})

    documento = mongo_db["sonda"].find_one({"_id": "r1"})

    assert mongo_db.name.endswith("_test")
    assert documento["collected_at"] == coletado
    assert documento["collected_at"].tzinfo is not None
