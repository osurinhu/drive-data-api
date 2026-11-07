"""Fiação do tratamento centralizado de erros.

As exceções de domínio levantadas pelo serviço viram código HTTP aqui — por
isso as rotas não têm try/except. O mapeamento mora em
``blueprint.http_errors.ERROR_STATUS``; este módulo só gera um handler por entrada.

Cuidado: o handler genérico de ``Exception`` **não pode** engolir
``HTTPException``, senão as respostas 422 de validação do flask-smorest (e todo
``abort()``) virariam um 500 genérico.
"""

from collections.abc import Callable

from flask import Flask, current_app
from werkzeug.exceptions import HTTPException

from blueprint.http_errors import ERROR_STATUS, STATUS_DESCRIPTION, error_response
from instrumentation.request_context import current_request_id
from service.errors import ServiceError


def _handler_for(status: int) -> Callable:
    def handler(error: Exception):
        return error_response(status, str(error) or STATUS_DESCRIPTION[status])

    return handler


def _handle_unmapped_service_error(error: ServiceError):
    """Rede de segurança: exceção de domínio que ninguém mapeou.

    `tests/test_contracts.py` impede que isso chegue a produção, mas se chegar,
    o log diz exatamente o que faltou mapear.
    """
    current_app.logger.error(
        "[req=%s] Exceção de serviço sem mapeamento HTTP em ERROR_STATUS: %s",
        current_request_id(),
        type(error).__name__,
    )
    return error_response(500, STATUS_DESCRIPTION[500])


def _handle_unexpected(error: Exception):
    # Se o flask-smorest não registrou seu próprio handler de HTTPException,
    # este genérico receberia também os erros HTTP — repassa a resposta original.
    if isinstance(error, HTTPException):
        return error.get_response()

    # O id vai no log E no corpo da resposta: é o que permite achar este
    # traceback a partir do que o cliente relatou.
    current_app.logger.exception("[req=%s] Exceção não tratada", current_request_id())
    return error_response(500, STATUS_DESCRIPTION[500])


def init_app(app: Flask) -> None:
    for exception, status in ERROR_STATUS.items():
        app.register_error_handler(exception, _handler_for(status))

    # O Flask resolve pela MRO, então estes dois só pegam o que os específicos
    # acima não pegaram.
    app.register_error_handler(ServiceError, _handle_unmapped_service_error)
    app.register_error_handler(Exception, _handle_unexpected)
