"""Testes contra o MongoDB de verdade, no banco ``<MONGO_DATABASE>_test``.

Cada teste começa com o banco vazio: as coleções (e os índices delas) são
apagadas depois de cada um. A limpeza das tabelas do Postgres, autouse no
conftest da raiz, é anulada aqui, e o ``mongo_db`` vem de uma aplicação mínima,
só com ``ext.mongo``: estes testes não usam o Postgres.
"""

import pytest


@pytest.fixture(autouse=True)
def _clean_tables():
    """Nada a limpar no Postgres."""
    yield


@pytest.fixture(autouse=True)
def _limpar_bronze(mongo_db):
    yield
    # A trava é o sufixo: a suíte apaga coleções, e o banco de desenvolvimento
    # nunca pode ser o alvo, nem por engano de configuração.
    assert mongo_db.name.endswith("_test"), mongo_db.name
    for colecao in mongo_db.list_collection_names():
        mongo_db.drop_collection(colecao)
