"""Schemas da leitura do bronze pela silver: a query, o cursor e a página.

O cursor é opaco para a silver: base64url (sem o ``=`` do fim) de um JSON
``{"v": 1, "t": <ingested_at em milissegundos>, "id": "<record_id>"}``. Ele é a
representação HTTP de uma ``BronzePosition``, e por isso o codec mora aqui; o serviço
só conhece a posição. Os milissegundos bastam: é a precisão da data no BSON, então a
posição volta exatamente como saiu.
"""

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from marshmallow import Schema, fields, validate

from domain.ports import BronzePosition

#: Os limites da página.
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000

#: A versão do formato do cursor. Formato novo é versão nova, e a anterior continua
#: aceita enquanto a silver puder ter um cursor dela guardado (na Fase 4, o v1 vale
#: como a leitura sem filtro). Versão desconhecida dá 422.
CURSOR_VERSION = 1
INVALID_CURSOR_MESSAGE = "Cursor inválido."

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


def encode_cursor(position: BronzePosition) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "t": (position.ingested_at - _EPOCH) // _MS,
        "id": str(position.record_id),
    }
    texto = json.dumps(payload, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(texto).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> BronzePosition:
    """A posição de um cursor emitido por ``encode_cursor``. Levanta ``ValueError``
    para qualquer outra coisa."""
    try:
        texto = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(texto)
    except (binascii.Error, ValueError) as error:  # inclui JSON e UTF-8 inválidos
        raise ValueError("cursor sem base64url e JSON") from error
    if not isinstance(payload, dict) or payload.keys() != {"v", "t", "id"}:
        raise ValueError("cursor com outras chaves")
    versao, instante, record_id = payload["v"], payload["t"], payload["id"]
    # `type(...) is int`: o `True` do JSON passaria por um `isinstance`.
    if type(versao) is not int or versao != CURSOR_VERSION:
        raise ValueError("versão do cursor desconhecida")
    if type(instante) is not int or instante < 0 or not isinstance(record_id, str):
        raise ValueError("cursor com tipos errados")
    try:
        return BronzePosition(ingested_at=_EPOCH + instante * _MS, record_id=UUID(record_id))
    except (OverflowError, ValueError) as error:
        raise ValueError("cursor fora do intervalo") from error


class CursorField(fields.String):
    """O cursor opaco na API; uma ``BronzePosition`` do lado de dentro."""

    default_error_messages = {"invalid_cursor": INVALID_CURSOR_MESSAGE}

    def _serialize(self, value: Any, attr: str | None, obj: Any, **kwargs: Any) -> str | None:
        texto = None if value is None else encode_cursor(value)
        return super()._serialize(texto, attr, obj, **kwargs)

    def _deserialize(self, value: Any, attr: str | None, data: Any, **kwargs: Any) -> Any:
        texto = super()._deserialize(value, attr, data, **kwargs)
        try:
            return decode_cursor(texto)
        except ValueError as error:
            raise self.make_error("invalid_cursor") from error


class BronzeQuerySchema(Schema):
    """Parâmetros da leitura, na query string."""

    cursor = CursorField(
        load_default=None,
        metadata={
            "description": "O `next_cursor` da resposta anterior. Sem ele, a leitura começa "
            "do primeiro registro do bronze."
        },
    )
    limit = fields.Int(
        load_default=DEFAULT_LIMIT,
        validate=validate.Range(min=1, max=MAX_LIMIT),
        metadata={"description": f"Registros por página (máximo {MAX_LIMIT})."},
    )


class BronzeRecordSchema(Schema):
    """Um registro do bronze como a silver o lê: o bruto e os metadados da ingestão.

    Os campos saem de ``domain.ports.BronzeEntry``; o ``tests/test_contracts.py``
    reprova o campo daqui que não exista lá.
    """

    record_id = fields.UUID(metadata={"description": "O `id` que o app deu ao registro."})
    vin = fields.Str(metadata={"description": "VIN na forma canônica (maiúsculas)."})
    vehicle_id = fields.Int()
    user_id = fields.Int(metadata={"description": "O remetente: o usuário do token do envio."})
    sender_relation = fields.Str(
        metadata={"description": "O vínculo que autorizou o envio: `owner` (gestor) ou `driver`."}
    )
    collected_at = fields.DateTime(metadata={"description": "Quando o app coletou as leituras."})
    received_at = fields.DateTime(metadata={"description": "Quando a API recebeu o lote."})
    ingested_at = fields.DateTime(
        metadata={
            "description": "Quando o bronze gravou o registro, pelo relógio do bronze. "
            "É a ordem de leitura."
        }
    )
    message_id = fields.UUID(
        metadata={"description": "O envelope da fila: o `batch_id` do recibo do envio."}
    )
    request_id = fields.Str(
        allow_none=True, metadata={"description": "O `X-Request-ID` da requisição do envio."}
    )
    schema_version = fields.Int(metadata={"description": "A versão do contrato da fila."})
    payload_user_matches_sender = fields.Bool(
        metadata={
            "description": "O campo `user` do registro bate com o login do remetente. "
            "Informativo: nunca autoriza nada."
        }
    )
    raw = fields.Dict(metadata={"description": "O registro exatamente como o app o enviou."})


class BronzePageSchema(Schema):
    """A página da leitura. Espelha ``service.pagination.CursorPage``."""

    items = fields.List(fields.Nested(BronzeRecordSchema))
    next_cursor = CursorField(
        metadata={
            "description": "De onde continuar. Guarde-o só depois de processar a página. Na "
            "página vazia, é o cursor recebido."
        }
    )
    has_more = fields.Bool(
        metadata={"description": "Há mais registros já disponíveis: chame de novo sem esperar."}
    )
    watermark = fields.DateTime(
        metadata={
            "description": "O limite desta leitura: a hora do bronze menos a janela de "
            "estabilização. O que foi gravado depois dele sai nas próximas chamadas."
        }
    )
