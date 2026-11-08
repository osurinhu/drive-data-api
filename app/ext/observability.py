"""O mínimo necessário para conseguir depurar em produção.

Configura os logs (JSON por padrão, ``LOG_FORMAT``), atribui um id a cada requisição e
emite o log de acesso. O id é devolvido no header ``X-Request-ID`` e entra no envelope
de erro e em toda linha de log, até nas do consumer, porque viaja no envelope da fila.

O log de acesso é da aplicação, e não do gunicorn: o ``--access-logformat`` não escapa
JSON e não conhece a rota nem o usuário.
"""

import logging
from time import perf_counter

from flask import Flask, Response, g, request

from instrumentation.correlation import (
    REQUEST_ID_HEADER,
    bind_correlation,
    clear_correlation,
    current_request_id,
    resolve_request_id,
)
from instrumentation.logs import configure_logging

access_logger = logging.getLogger("http.access")

#: O Prometheus pergunta a cada 15 s: uma linha por coleta afogaria o log.
_NOT_LOGGED = "/metrics"


def _log_access(response: Response) -> None:
    if request.path == _NOT_LOGGED:
        return
    route = request.url_rule.rule if request.url_rule else None
    access_logger.info(
        "%s %s %d",
        request.method,
        request.path,
        response.status_code,
        extra={
            "event": "http.access",
            "method": request.method,
            "path": request.path,
            "route": route,
            "status": response.status_code,
            "duration_ms": round((perf_counter() - g.request_started_at) * 1000, 1),
            "remote": request.remote_addr,
        },
    )


def init_app(app: Flask) -> None:
    level = str(app.config.get("LOG_LEVEL", "INFO")).upper()
    process = str(app.config.get("PROCESS") or "default")
    configure_logging(level, str(app.config.get("LOG_FORMAT", "json")), process=process)
    app.logger.setLevel(level)

    @app.before_request
    def _start_request() -> None:
        clear_correlation()
        bind_correlation(request_id=resolve_request_id(request.headers.get(REQUEST_ID_HEADER)))
        g.request_started_at = perf_counter()

    @app.after_request
    def _finish_request(response: Response) -> Response:
        request_id = current_request_id()
        if request_id:
            response.headers[REQUEST_ID_HEADER] = request_id
        _log_access(response)
        return response

    @app.teardown_request
    def _end_correlation(_error: BaseException | None) -> None:
        clear_correlation()
