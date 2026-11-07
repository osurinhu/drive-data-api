"""A sonda e a API de gerenciamento contra o RabbitMQ de verdade."""

from urllib.parse import urlsplit, urlunsplit

import pytest

from messaging.connection import broker_reachable, connection_parameters
from messaging.management import ManagementApi, ManagementApiError


def _parametros(url):
    return connection_parameters(url, timeout=5, heartbeat=60, connection_name="pytest")


def _trocar(url, *, senha=None, vhost=None):
    partes = urlsplit(url)
    netloc = partes.netloc
    if senha is not None:
        credencial, _, endereco = netloc.rpartition("@")
        netloc = f"{credencial.partition(':')[0]}:{senha}@{endereco}"
    return urlunsplit(partes._replace(netloc=netloc, path=f"/{vhost}" if vhost else partes.path))


def test_a_sonda_responde_ok_no_vhost_de_testes(amqp_url):
    assert broker_reachable(_parametros(amqp_url)) is True


def test_a_sonda_verifica_o_vhost_e_nao_so_a_porta(amqp_url):
    """O broker atende, mas o vhost não existe: não adianta para quem publica."""
    assert broker_reachable(_parametros(_trocar(amqp_url, vhost="vhost-que-nao-existe"))) is False


def test_a_sonda_verifica_a_credencial(amqp_url):
    assert broker_reachable(_parametros(_trocar(amqp_url, senha="senha-errada"))) is False


def test_garantir_o_vhost_de_novo_nao_muda_nada(amqp_management, amqp_vhost, amqp_url):
    """O PUT da API de gerenciamento é idempotente: o setup pode rodar sempre."""
    amqp_management.ensure_vhost(amqp_vhost)

    assert broker_reachable(_parametros(amqp_url)) is True


def test_a_api_de_gerenciamento_recusa_credencial_errada(amqp_management_url, amqp_vhost):
    """O erro diz o status e não repete a credencial."""
    gerenciamento = ManagementApi(_trocar(amqp_management_url, senha="senha-errada"), timeout=5)

    with pytest.raises(ManagementApiError, match="401") as erro:
        gerenciamento.ensure_vhost(amqp_vhost)

    assert "senha-errada" not in str(erro.value)
