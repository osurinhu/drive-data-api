"""Confere no Prometheus de verdade o que o pipeline acabou de produzir.

Roda dentro do container do api (só stdlib) e alcança o Prometheus pela rede do projeto.
Espera a coleta alcançar o que o roteiro fez: o intervalo é de 15 s.
"""

import json
import sys
import time
import urllib.parse
import urllib.request

PROMETHEUS = "http://prometheus:9090/api/v1"
PRAZO_SEGUNDOS = 90
ALVOS = {"api", "bronze-api", "consumer", "rabbitmq"}

#: Tem de existir ao menos uma série em cada uma destas consultas.
CONSULTAS = {
    "recusas no envio": 'ingest_records_total{outcome="rejected"}',
    "registros aceitos": 'ingest_records_total{outcome="accepted"}',
    "http por rota": 'http_requests_total{job="api",route="/api/v1/telemetry/batches"}',
    "mensagens confirmadas": 'consumer_messages_total{outcome="acked"}',
    "registros no bronze": 'bronze_records_total{outcome="inserted"}',
    "latência do pipeline": 'pipeline_latency_seconds_count{job="consumer"}',
    "idade do carimbo": 'bronze_stamp_age_seconds_count{job="consumer"}',
    "consumer pronto": 'consumer_ready{job="consumer"} == 1',
    "atraso da silver": 'bronze_silver_lag_seconds{job="bronze-api"}',
    "leitura da silver": "bronze_read_records_total",
    "fila": 'rabbitmq_queue_messages{queue="bronze.telemetry"}',
    "bytes da fila": 'rabbitmq_queue_messages_bytes{queue="bronze.telemetry"}',
    "DLQ": 'rabbitmq_queue_messages{queue="bronze.telemetry.dlq"}',
    "taxa de descarte": (
        'sum(rate(ingest_records_total{outcome="rejected"}[2m]))'
        " / sum(rate(ingest_records_total[2m]))"
    ),
}


def _api(caminho: str) -> dict:
    with urllib.request.urlopen(f"{PROMETHEUS}/{caminho}", timeout=5) as resposta:
        return json.load(resposta)["data"]


def _series(expressao: str) -> list:
    return _api(f"query?query={urllib.parse.quote(expressao)}")["result"]


def _alvos_de_pe() -> set[str]:
    return {
        alvo["labels"]["job"] for alvo in _api("targets")["activeTargets"] if alvo["health"] == "up"
    }


def _pendencias() -> list[str]:
    faltam = [f"alvo {alvo}" for alvo in sorted(ALVOS - _alvos_de_pe())]
    return faltam + [nome for nome, expressao in CONSULTAS.items() if not _series(expressao)]


fim = time.monotonic() + PRAZO_SEGUNDOS
while True:
    try:
        pendencias = _pendencias()
    except OSError as erro:
        pendencias = [f"Prometheus não respondeu: {erro}"]
    if not pendencias or time.monotonic() > fim:
        break
    time.sleep(3)

falhou = bool(pendencias)
if falhou:
    print(f"FALHOU  no Prometheus, faltou: {', '.join(pendencias)}")
else:
    print(f"OK      Prometheus coletou os {len(ALVOS)} alvos e as {len(CONSULTAS)} consultas")

regras = [r["name"] for g in _api("rules")["groups"] for r in g["rules"]]
if len(regras) >= 5:
    print(f"OK      alertas carregados: {', '.join(regras)}")
else:
    print(f"FALHOU  alertas carregados: {regras}")
    falhou = True

sys.exit(1 if falhou else 0)
