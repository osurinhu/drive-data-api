"""Testes sem infraestrutura: nem banco, nem Mongo, nem RabbitMQ.

Anula a limpeza de tabelas do conftest da raiz, que é autouse e dependeria do
``app`` — e o ``app`` sobe o banco. Teste desta pasta que peça ``app``,
``client`` ou qualquer fixture de serviço externo está na pasta errada.
"""

import pytest


@pytest.fixture(autouse=True)
def _clean_tables():
    """Nada a limpar: os testes desta pasta não tocam em banco."""
    yield
