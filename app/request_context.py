"""Identificador da requisição atual.

Módulo folha: não importa nada da aplicação, então serve tanto à extensão que
instala os hooks (``ext.observability``) quanto ao utilitário que monta as
respostas de erro (``http_errors``), sem criar dependência entre eles.
"""

import re
import uuid

from flask import g, has_request_context, request

REQUEST_ID_HEADER = "X-Request-ID"

#: Id vindo do cliente é entrada hostil e vai direto para o log: só estes
#: caracteres passam, e no máximo este tamanho.
_SAFE_REQUEST_ID = re.compile(r"[^A-Za-z0-9._-]")
_MAX_REQUEST_ID_LENGTH = 64


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _sanitize_request_id(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _SAFE_REQUEST_ID.sub("", value)[:_MAX_REQUEST_ID_LENGTH]
    return cleaned or None


def resolve_request_id() -> str:
    """Id a usar nesta requisição: o do cliente, se válido; senão, um novo."""
    return _sanitize_request_id(request.headers.get(REQUEST_ID_HEADER)) or _new_request_id()


def current_request_id() -> str | None:
    """Id da requisição atual, ou ``None`` fora de um contexto de requisição."""
    if not has_request_context():
        return None
    return g.get("request_id")
