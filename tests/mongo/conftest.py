"""Testes contra o MongoDB de verdade, no banco ``<MONGO_DATABASE>_test``.

Cada teste começa com o banco vazio: as coleções (e os índices delas) são
apagadas depois de cada um. A limpeza das tabelas do Postgres, autouse no
conftest da raiz, é anulada aqui, e o ``mongo_db`` vem de uma aplicação mínima,
só com ``ext.mongo``: estes testes não usam o Postgres. O contexto dessa aplicação
fica aberto durante cada teste, porque os adaptadores de ``repository/mongo``
pedem o banco à ``ext.mongo`` (``mongo.db``).
"""

import pytest


@pytest.fixture(autouse=True)
def _clean_tables():
    """Nada a limpar no Postgres."""
    yield


@pytest.fixture(autouse=True)
def _contexto_do_mongo(_app_do_mongo):
    """O ``current_app`` dos adaptadores durante o teste."""
    with _app_do_mongo.app_context():
        yield


@pytest.fixture(autouse=True)
def _limpar_bronze(bronze_limpo):
    """O banco de testes do Mongo é apagado depois de cada teste (``bronze_limpo``)."""
    return bronze_limpo
