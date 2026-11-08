"""Os adaptadores das portas de observação: o que cada acontecimento conta e registra.

Os instrumentos são globais do processo, então cada teste compara antes e depois.
"""

import logging
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY

from instrumentation.correlation import clear_correlation, current_correlation
from instrumentation.metrics import (
    BronzeMetrics,
    BronzeReadMetrics,
    ConsumerMetrics,
    IngestMetrics,
    render,
)


def _valor(nome: str, **rotulos) -> float:
    return REGISTRY.get_sample_value(nome, rotulos) or 0.0


class Delta:
    def __init__(self, nome: str, **rotulos) -> None:
        self._nome, self._rotulos = nome, rotulos
        self._antes = _valor(nome, **rotulos)

    @property
    def valor(self) -> float:
        return _valor(self._nome, **self._rotulos) - self._antes


def _eventos(caplog) -> list[str]:
    return [r.event for r in caplog.records if hasattr(r, "event")]


@pytest.fixture(autouse=True)
def _tudo_no_log(caplog):
    with caplog.at_level(logging.DEBUG, logger="instrumentation.metrics"):
        yield
    clear_correlation()


def test_recusa_conta_por_motivo_e_registra_o_evento(caplog):
    recusas = Delta("ingest_records_total", outcome="rejected", reason="vehicle_not_authorized")

    IngestMetrics().record_rejected(
        record_id=uuid4(), vin="X" * 17, reason="vehicle_not_authorized"
    )

    assert recusas.valor == 1
    assert _eventos(caplog) == ["telemetry.record.rejected"]


def test_lote_publicado_conta_registros_e_divergencia_do_usuario(caplog, novo_lote):
    lote = novo_lote(registros=3)
    divergente = replace(lote.records[0], payload_user_matches_sender=False)
    lote = replace(lote, records=(divergente, *lote.records[1:]))
    aceitos = Delta("ingest_records_total", outcome="accepted", reason="")
    lotes = Delta("ingest_batches_total", outcome="published")
    divergencias = Delta("payload_user_mismatch_total")

    IngestMetrics().batch_published(lote, publish_seconds=0.02)

    assert (aceitos.valor, lotes.valor, divergencias.valor) == (3, 1, 1)
    assert _eventos(caplog) == ["telemetry.batch.published"]


def test_falha_de_publicacao_conta_e_avisa(caplog, novo_lote):
    falhas = Delta("ingest_batches_total", outcome="publish_failed")

    IngestMetrics().publish_failed(novo_lote(), reason="sem rota")

    assert falhas.valor == 1
    assert _eventos(caplog) == ["telemetry.publish.failed"]


def test_gravacao_conta_cada_destino_e_mede_a_latencia(caplog, novo_lote):
    lote = novo_lote()
    colidido, invalido, duplicado = uuid4(), uuid4(), uuid4()
    inseridos = Delta("bronze_records_total", outcome="inserted")
    colisoes = Delta("bronze_records_total", outcome="collision")
    duplicados = Delta("bronze_records_total", outcome="duplicate")
    invalidos = Delta("bronze_records_total", outcome="invalid")
    latencias = Delta("pipeline_latency_seconds_count")
    soma = Delta("pipeline_latency_seconds_sum")
    carimbos = Delta("bronze_stamp_age_seconds_count")

    BronzeMetrics().batch_ingested(
        lote,
        ingested_at=lote.received_at + timedelta(seconds=2),
        stamp_age_seconds=0.1,
        inserted=4,
        duplicates={duplicado},
        collisions={colidido},
        invalid={invalido},
    )

    assert (inseridos.valor, colisoes.valor, duplicados.valor, invalidos.valor) == (4, 1, 1, 1)
    assert (latencias.valor, carimbos.valor) == (1, 1)
    assert soma.valor == pytest.approx(2)
    assert _eventos(caplog) == [
        "bronze.batch.ingested",
        "bronze.record.duplicate",
        "bronze.record.collision",
        "bronze.record.invalid",
    ]


def test_leitura_com_filtro_nao_mexe_no_atraso():
    observador = BronzeReadMetrics()
    observador.page_read(records=2, lag_seconds=12.5)
    registros = Delta("bronze_read_records_total")

    observador.page_read(records=3, lag_seconds=None)

    assert registros.valor == 3
    assert _valor("bronze_silver_lag_seconds") == 12.5


def test_o_consumer_correlaciona_cada_mensagem_e_limpa_no_fim():
    observador = ConsumerMetrics()

    observador.message_started(message_id="m", request_id="r", user_id=51)
    correlacao = current_correlation()
    observador.message_finished()

    assert correlacao == {"message_id": "m", "request_id": "r", "user_id": 51}
    assert current_correlation() == {}


@pytest.mark.parametrize(
    ("chamar", "outcome"),
    [
        (lambda o: o.acked(seconds=0.1), "acked"),
        (lambda o: o.dead_lettered(reason="fora"), "dead_lettered"),
        (lambda o: o.requeued(reason="bronze_unavailable"), "requeued"),
    ],
    ids=["ack", "DLQ", "de volta à fila"],
)
def test_cada_destino_da_mensagem_tem_o_seu_contador(chamar, outcome):
    contagem = Delta("consumer_messages_total", outcome=outcome)

    chamar(ConsumerMetrics())

    assert contagem.valor == 1


def test_pausa_e_volta_viram_eventos(caplog):
    observador = ConsumerMetrics()

    observador.paused()
    observador.resumed()

    assert _eventos(caplog) == ["consumer.paused", "consumer.resumed"]


def test_render_devolve_o_formato_do_prometheus():
    corpo, tipo = render()

    assert b"consumer_messages_total" in corpo
    assert tipo.startswith("text/plain")
