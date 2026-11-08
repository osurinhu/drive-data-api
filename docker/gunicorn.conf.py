"""Gunicorn com métricas do Prometheus em modo multiprocess.

Cada worker é um processo, com os próprios contadores: sem isto, cada coleta do
``/metrics`` leria um worker qualquer. A variável precisa existir antes de o worker
importar o ``prometheus_client``, por isso nasce aqui, e só no processo do gunicorn:
o consumer e os comandos ``flask`` continuam com o registro em memória.
"""

import os
from pathlib import Path

_METRICS_DIR = Path("/tmp/prometheus-multiproc")
os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", str(_METRICS_DIR))


def on_starting(_server) -> None:
    """Começa do zero: arquivos de uma execução anterior somariam contagens velhas."""
    _METRICS_DIR.mkdir(parents=True, exist_ok=True)
    for arquivo in _METRICS_DIR.glob("*.db"):
        arquivo.unlink()


def child_exit(_server, worker) -> None:
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)
