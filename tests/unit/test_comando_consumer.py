"""O comando ``flask consumer run``, sem broker: o sinal pede a parada, e a queda do
broker sai com erro (no compose, o ``restart`` sobe o processo de novo)."""

import signal
from types import SimpleNamespace

import pytest
from flask import Flask

from ext import consumer as ext_consumer
from messaging.consumer import ConsumerFailed


class ConsumidorFalso:
    def __init__(self, falha: Exception | None = None) -> None:
        self.falha = falha
        self.parado = False

    def stop(self) -> None:
        self.parado = True

    def run(self) -> None:
        if self.falha is not None:
            raise self.falha


@pytest.fixture
def sinais(monkeypatch):
    """Os handlers que o comando instala, sem trocar os do processo do pytest."""
    instalados: dict = {}
    monkeypatch.setattr(
        signal, "signal", lambda sinal, handler: instalados.update({sinal: handler})
    )
    return instalados


def _invocar(consumidor: ConsumidorFalso):
    aplicacao = Flask("consumer-de-teste")
    aplicacao.consumer_container = SimpleNamespace(telemetry_consumer=lambda: consumidor)
    ext_consumer.init_app(aplicacao)
    return aplicacao.test_cli_runner().invoke(args=["consumer", "run"])


def test_sigterm_e_sigint_pedem_a_parada_do_consumidor(sinais):
    consumidor = ConsumidorFalso()

    resultado = _invocar(consumidor)
    sinais[signal.SIGTERM](signal.SIGTERM, None)

    assert resultado.exit_code == 0, resultado.output
    assert set(sinais) == {signal.SIGTERM, signal.SIGINT}
    assert consumidor.parado


@pytest.mark.usefixtures("sinais")
def test_a_queda_do_broker_sai_com_erro_e_o_motivo():
    resultado = _invocar(ConsumidorFalso(falha=ConsumerFailed("StreamLostError: conexão perdida")))

    assert resultado.exit_code == 1
    assert "StreamLostError" in resultado.output
