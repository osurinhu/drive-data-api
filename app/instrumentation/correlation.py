"""Os ids que ligam uma linha de log à requisição e à mensagem que a causaram.

Vivem em ``contextvars``, e não no ``g`` do Flask, porque o consumer não tem
requisição: ele os põe aqui a cada mensagem, e o formato dos logs os lê sem saber de
que processo se trata. Sem nada da aplicação nem o Flask, serve a extensões, rotas e
adaptadores.
"""

import re
import uuid
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"

#: Id vindo do cliente é entrada hostil e vai direto para o log: só estes
#: caracteres passam, e no máximo este tamanho.
_SAFE_REQUEST_ID = re.compile(r"[^A-Za-z0-9._-]")
_MAX_REQUEST_ID_LENGTH = 64

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_message_id: ContextVar[str | None] = ContextVar("message_id", default=None)
_user_id: ContextVar[int | None] = ContextVar("user_id", default=None)


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _sanitize_request_id(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _SAFE_REQUEST_ID.sub("", value)[:_MAX_REQUEST_ID_LENGTH]
    return cleaned or None


def resolve_request_id(header_value: str | None) -> str:
    """Id a usar nesta requisição: o do cliente, se válido; senão, um novo."""
    return _sanitize_request_id(header_value) or _new_request_id()


def bind_correlation(
    *,
    request_id: str | None = None,
    message_id: str | None = None,
    user_id: int | None = None,
) -> None:
    """Acrescenta os ids informados aos que já valem."""
    if request_id is not None:
        _request_id.set(request_id)
    if message_id is not None:
        _message_id.set(message_id)
    if user_id is not None:
        _user_id.set(user_id)


def clear_correlation() -> None:
    """Fim da requisição ou da mensagem: o processo reaproveita a thread."""
    _request_id.set(None)
    _message_id.set(None)
    _user_id.set(None)


def current_request_id() -> str | None:
    """Id da requisição, ou da mensagem que a continua; ``None`` fora delas."""
    return _request_id.get()


def current_correlation() -> dict[str, str | int]:
    """Os ids que valem agora, sem os vazios: o que entra em cada linha de log."""
    values = {
        "request_id": _request_id.get(),
        "message_id": _message_id.get(),
        "user_id": _user_id.get(),
    }
    return {name: value for name, value in values.items() if value is not None}
