"""O ``/metrics`` dos processos HTTP e a contagem das requisições.

Fica fora de ``/api/v1`` de propósito: não é API do produto, não aparece no OpenAPI e
é bloqueado no proxy reverso (02-decisoes, "As métricas ficam fora da API"). O consumer
não carrega esta extensão: expõe as dele no servidor de saúde.
"""

from time import perf_counter

from flask import Flask, Response, g, request

from instrumentation.metrics import observe_http, render

METRICS_PATH = "/metrics"
#: Rota que o roteador não reconheceu: o caminho cru daria uma série por URL inventada.
_UNMATCHED = "unmatched"


def _expose() -> Response:
    body, content_type = render()
    return Response(body, content_type=content_type)


def init_app(app: Flask) -> None:
    app.add_url_rule(METRICS_PATH, "metrics", _expose)

    @app.before_request
    def _start_timer() -> None:
        g.metrics_started_at = perf_counter()

    @app.after_request
    def _count(response: Response) -> Response:
        if request.path != METRICS_PATH:
            route = request.url_rule.rule if request.url_rule else _UNMATCHED
            observe_http(route, response.status_code, perf_counter() - g.metrics_started_at)
        return response
