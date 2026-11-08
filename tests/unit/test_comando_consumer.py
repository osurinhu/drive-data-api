"""O comando ``flask consumer run`` e a reconexão, sem broker: o sinal pede a parada, e
a queda do broker não encerra o processo."""

import signal
import socket
import time
from types import SimpleNamespace

import pytest
from flask import Flask

from dubles import ObservadorEmMemoria
from ext import consumer as ext_consumer
from instrumentation.probes import register_probe
from messaging.consumer import ConsumerFailed, RabbitTelemetryConsumer


class ConsumidorFalso:
    def __init__(self) -> None:
        self.parado = False
        self.rodou = False

    def stop(self) -> None:
        self.parado = True

    def is_consuming(self) -> bool:
        return True

    def run_until_stopped(self) -> None:
        self.rodou = True


@pytest.fixture
def sinais(monkeypatch):
    """Os handlers que o comando instala, sem trocar os do processo do pytest."""
    instalados: dict = {}
    monkeypatch.setattr(
        signal, "signal", lambda sinal, handler: instalados.update({sinal: handler})
    )
    return instalados


def _invocar(consumidor: ConsumidorFalso, porta: int = 0):
    aplicacao = Flask("consumer-de-teste")
    aplicacao.config["CONSUMER_HEALTH_PORT"] = porta
    aplicacao.consumer_container = SimpleNamespace(telemetry_consumer=lambda: consumidor)
    register_probe(aplicacao, "mongo", lambda: True)
    ext_consumer.init_app(aplicacao)
    return aplicacao.test_cli_runner().invoke(args=["consumer", "run"])


def test_sigterm_e_sigint_pedem_a_parada_do_consumidor(sinais):
    consumidor = ConsumidorFalso()

    resultado = _invocar(consumidor)
    sinais[signal.SIGTERM](signal.SIGTERM, None)

    assert resultado.exit_code == 0, resultado.output
    assert set(sinais) == {signal.SIGTERM, signal.SIGINT}
    assert consumidor.rodou
    assert consumidor.parado


@pytest.mark.usefixtures("sinais")
def test_porta_de_saude_ocupada_reprova_a_subida_com_o_motivo():
    consumidor = ConsumidorFalso()
    with socket.socket() as ocupada:
        ocupada.bind(("0.0.0.0", 0))
        ocupada.listen()

        resultado = _invocar(consumidor, porta=ocupada.getsockname()[1])

    assert resultado.exit_code == 1
    assert "porta de saúde" in resultado.output
    assert not consumidor.rodou


# --- A reconexão -------------------------------------------------------------------


@pytest.fixture
def consumidor_real():
    """O consumidor de verdade, sem conectar: os testes trocam o ``run``."""
    return RabbitTelemetryConsumer(
        "amqp://ninguem:nada@127.0.0.1:1/teste",
        timeout=1,
        heartbeat=60,
        connection_name="pytest:consumer",
        prefetch=1,
        handler=lambda _batch: None,
        observer=ObservadorEmMemoria(),
        retry_initial=1.0,
        retry_max=4.0,
    )


def _roteiro(consumidor, monkeypatch, passos):
    """Cada passo é uma chamada do ``run``: quantos segundos a conexão ficou de pé antes
    de cair, ou ``None`` se nem conectou. Acabados os passos, pede a parada. Devolve as
    esperas."""
    restantes = list(passos)
    esperas: list[float] = []

    def run():
        if not restantes:
            consumidor.stop()
            return
        de_pe = restantes.pop(0)
        consumidor._subscribed_at = None if de_pe is None else time.monotonic() - de_pe
        raise ConsumerFailed("broker fora (dublê)")

    monkeypatch.setattr(consumidor, "run", run)
    monkeypatch.setattr(consumidor, "_wait_disconnected", esperas.append)
    return esperas


def test_a_queda_do_broker_reconecta_em_vez_de_encerrar(consumidor_real, monkeypatch):
    esperas = _roteiro(consumidor_real, monkeypatch, [None])

    consumidor_real.run_until_stopped()

    assert len(esperas) == 1


def test_a_espera_entre_as_tentativas_dobra_ate_o_teto(consumidor_real, monkeypatch):
    esperas = _roteiro(consumidor_real, monkeypatch, [None] * 4)

    consumidor_real.run_until_stopped()

    assert esperas == [1.0, 2.0, 4.0, 4.0]


def test_a_conexao_que_ficou_de_pe_zera_a_espera(consumidor_real, monkeypatch):
    esperas = _roteiro(consumidor_real, monkeypatch, [None, None, 100, None])

    consumidor_real.run_until_stopped()

    assert esperas == [1.0, 2.0, 1.0, 2.0]


def test_a_conexao_que_cai_logo_depois_de_inscrever_nao_zera_a_espera(consumidor_real, monkeypatch):
    """Cada queda com mensagens em voo conta para o limite de entregas: uma conexão
    instável não pode reconectar sem espera."""
    esperas = _roteiro(consumidor_real, monkeypatch, [0, 0, 0])

    consumidor_real.run_until_stopped()

    assert esperas == [1.0, 2.0, 4.0]


def test_a_espera_sem_conexao_para_com_o_stop(consumidor_real, monkeypatch):
    """Sem isto, o SIGTERM esperaria o fim do intervalo, e o compose mataria o processo."""
    dormiu = []

    def sleep(segundos):
        dormiu.append(segundos)
        consumidor_real.stop()

    monkeypatch.setattr("messaging.consumer.time.sleep", sleep)

    consumidor_real._wait_disconnected(30.0)

    assert len(dormiu) == 1
