"""Envelope de erro HTTP e mapeamento das exceções de domínio.

Módulo folha, fora de ``ext/``: ``error_response`` é usado por duas extensões
(``ext.error_handler`` e ``ext.auth``), e mantê-lo aqui preserva a invariante de
que nenhuma extensão importa outra.
"""

from collections.abc import Callable
from typing import Any

from flask import jsonify
from werkzeug.http import HTTP_STATUS_CODES

from request_context import current_request_id
from schemas.error_schema import ApiErrorSchema
from service.errors import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ServiceError,
)

#: Exceção de domínio -> código HTTP. **Única** fonte de verdade do mapeamento:
#: ``ext.error_handler`` registra um handler por entrada, e
#: ``tests/test_contracts.py`` reprova se uma subclasse de ServiceError ficar de
#: fora. Exceção nova = uma linha aqui, e nada mais.
ERROR_STATUS: dict[type[ServiceError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    BusinessRuleError: 422,
    PermissionDeniedError: 403,
    AuthenticationError: 401,
}

#: Serve como mensagem padrão (exceção levantada sem texto) e como descrição
#: na documentação OpenAPI.
STATUS_DESCRIPTION: dict[int, str] = {
    400: "Requisição malformada",
    401: "Credenciais ausentes ou inválidas",
    403: "Acesso negado",
    404: "Recurso não encontrado",
    409: "Conflito com um registro existente",
    422: "Falha de validação ou de regra de negócio",
    500: "Erro interno no servidor",
    503: "Serviço indisponível",
}


def error_response(code: int, message: str):
    """Envelope de erro padrão — mesmo formato que o flask-smorest produz.

    Inclui o ``request_id`` quando disponível: é o que permite ligar a resposta
    que o cliente recebeu à linha de log correspondente no servidor.
    """
    payload: dict[str, Any] = {
        "code": code,
        "status": HTTP_STATUS_CODES.get(code, "Error"),
        "message": message,
    }

    request_id = current_request_id()
    if request_id:
        payload["request_id"] = request_id

    return jsonify(payload), code


def error_responses(blp, *codes: int) -> Callable:
    """Documenta respostas de erro no OpenAPI com uma linha em vez de várias:
    ``@error_responses(blp, 404, 409)``.

    Liste o que **esta** rota produz. O 401 fica de fora: a exigência de token
    é global no spec (``ext.api``). O 422 também: o smorest o acrescenta sozinho
    onde há ``@blp.arguments``.
    """

    def decorator(fn: Callable) -> Callable:
        for code in reversed(codes):
            fn = blp.alt_response(
                code,
                schema=ApiErrorSchema,
                description=STATUS_DESCRIPTION.get(code, "Erro"),
            )(fn)
        return fn

    return decorator
