"""Testes contra o RabbitMQ de verdade, no vhost de testes (``drive-data-test``).

O vhost é garantido uma vez por sessão (fixture ``amqp_vhost``, do conftest da
raiz). A limpeza das tabelas do Postgres, autouse na raiz, é anulada aqui.
"""

import pytest


@pytest.fixture(autouse=True)
def _clean_tables():
    """Nada a limpar no Postgres."""
    yield


@pytest.fixture(autouse=True)
def _vhost_de_testes(amqp_vhost):
    """Todo teste desta pasta usa o vhost de testes; ele precisa existir antes."""
    return amqp_vhost
