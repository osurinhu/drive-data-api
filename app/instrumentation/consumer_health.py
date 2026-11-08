"""O servidor de saúde do consumer, numa thread do próprio processo.

O consumer não tem HTTP: os dois caminhos daqui são o que o healthcheck do compose (e
um orquestrador) pergunta a ele. O servidor HTTP é o da stdlib: são poucos caminhos, e
o processo não tem servidor WSGI.

* ``/health/live``: o processo está de pé;
* ``/health/ready``: conectado ao broker, consumindo e com o Mongo respondendo;
* ``/metrics``: as métricas do processo, para o Prometheus.

O Mongo é sondado à parte da fila porque, com a fila vazia, ninguém descobriria que
ele caiu. O ``ping`` que deu certo vale por ``bronze_ttl`` segundos, para o health não
virar carga; o que falhou não fica guardado, e a volta do Mongo aparece na pergunta
seguinte.
"""

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from instrumentation.metrics import CONSUMER_READY, render

logger = logging.getLogger(__name__)


class _QuietServer(ThreadingHTTPServer):
    """Cliente que desiste no meio não é erro: o padrão imprimiria um traceback no stderr."""

    def handle_error(self, _request: Any, client_address: Any) -> None:
        logger.debug("Pergunta de saúde interrompida por %s.", client_address, exc_info=True)


class ConsumerHealthServer:
    def __init__(
        self,
        port: int,
        *,
        consuming: Callable[[], bool],
        bronze_ok: Callable[[], bool],
        bronze_ttl: float = 30.0,
        # Dentro do container: a porta não é publicada no host.
        host: str = "0.0.0.0",
    ) -> None:
        self._address = (host, port)
        self._consuming = consuming
        self._bronze_ok = bronze_ok
        self._bronze_ttl = bronze_ttl
        self._lock = threading.Lock()
        self._bronze_ok_at = -math.inf
        self._server: _QuietServer | None = None

    @property
    def port(self) -> int:
        """A porta em que escuta (a escolhida pelo sistema, quando pedida a 0)."""
        assert self._server is not None
        return self._server.server_address[1]

    def start(self) -> None:
        """Abre a porta e atende numa thread daemon. Levanta ``OSError`` se ela estiver em uso."""
        self._server = _QuietServer(self._address, self._handler_class())
        threading.Thread(
            target=self._server.serve_forever, name="consumer-health", daemon=True
        ).start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    def readiness(self) -> tuple[int, dict[str, str]]:
        consuming = self._consuming()
        bronze = self._bronze_recently_ok()
        ready = consuming and bronze
        return 200 if ready else 503, {
            "status": "ok" if ready else "unavailable",
            "consuming": "yes" if consuming else "no",
            "mongo": "ok" if bronze else "unreachable",
        }

    def _bronze_recently_ok(self) -> bool:
        # O lock também impede que perguntas simultâneas abram um ping cada uma.
        with self._lock:
            if time.monotonic() - self._bronze_ok_at < self._bronze_ttl:
                return True
            ok = self._bronze_ok()
            if ok:
                self._bronze_ok_at = time.monotonic()
            return ok

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        health = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/health/live":
                    self._reply(200, {"status": "ok"})
                elif self.path == "/health/ready":
                    self._reply(*health.readiness())
                elif self.path == "/metrics":
                    status, _ = health.readiness()
                    CONSUMER_READY.set(1 if status == 200 else 0)
                    self._send(200, *render())
                else:
                    self._reply(404, {"status": "not found"})

            def _reply(self, status: int, body: dict[str, Any]) -> None:
                self._send(status, json.dumps(body).encode(), "application/json")

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                # Uma linha por healthcheck afogaria o log do consumer.
                logger.debug(format, *args)

        return Handler
