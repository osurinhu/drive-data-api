"""O mínimo necessário para conseguir depurar em produção.

Instala um id por requisição e configura o nível de log. O id é devolvido no
header ``X-Request-ID`` e incluído no envelope de erro e nas linhas de log —
sem ele, um 500 em produção dá ao cliente "Erro interno no servidor." e não há
como achar o traceback correspondente.
"""

import logging

from flask import Flask, g

from instrumentation.request_context import REQUEST_ID_HEADER, resolve_request_id

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def init_app(app: Flask) -> None:
    level = str(app.config.get("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(level=level, format=LOG_FORMAT)
    app.logger.setLevel(level)

    @app.before_request
    def _assign_request_id() -> None:
        g.request_id = resolve_request_id()

    @app.after_request
    def _echo_request_id(response):
        request_id = g.get("request_id")
        if request_id:
            response.headers[REQUEST_ID_HEADER] = request_id
        return response
