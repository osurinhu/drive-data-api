"""Testes que exigem as três infraestruturas: Postgres, Mongo e RabbitMQ.

A limpeza das tabelas do Postgres, autouse no conftest da raiz, vale aqui. O
vhost de testes do RabbitMQ é garantido antes de cada teste, e o banco de testes do
Mongo é apagado depois de cada um.
"""

import pytest


@pytest.fixture(autouse=True)
def _vhost_de_testes(amqp_vhost):
    """Todo teste desta pasta pode usar o vhost de testes; ele precisa existir antes."""
    return amqp_vhost


@pytest.fixture(autouse=True)
def _limpar_bronze(bronze_limpo):
    """O banco de testes do Mongo é apagado depois de cada teste (``bronze_limpo``)."""
    return bronze_limpo
