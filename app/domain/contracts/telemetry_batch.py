"""Contrato ``telemetry.batch.v1``: o lote aceito pelo producer, a caminho do bronze.

Um envelope por lote, só com os registros autorizados, cada um com o veredito da
posse (``vehicle_id``, ``sender_relation``) e o registro bruto (``raw``),
exatamente como o app o enviou.

O endereço da mensagem (``EXCHANGE`` e ``ROUTING_KEY``) também é do contrato:
quem publica não precisa conhecer fila, DLQ nem policy (``messaging.topology``).

Versionamento:

* campo novo e opcional não muda a versão: o decoder é um *tolerant reader* e
  ignora o que não conhece;
* remover, renomear ou mudar o significado de um campo cria a v2 (``ROUTING_KEY``
  ``telemetry.batch.v2``); o consumer aceita as duas durante a transição, e o
  producer só troca quando todos os consumers já aceitam a nova.

Os valores de ``sender_relation`` são do contrato, e não de
``domain.ports.SenderRelation``: um vínculo novo no domínio não pode entrar na v1
por tabela. ``tests/unit/test_contrato_telemetria.py`` reprova a divergência, e
quem a resolve decide se é campo novo ou versão nova.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from marshmallow import EXCLUDE, Schema, ValidationError, fields, post_load, validate

SCHEMA_VERSION = 1
EXCHANGE = "telemetry"
ROUTING_KEY = "telemetry.batch.v1"
CONTENT_TYPE = "application/json"
CONTENT_ENCODING = "utf-8"

#: Os vínculos que a v1 conhece.
SENDER_RELATIONS = ("owner", "driver")


class ContractError(ValueError):
    """A mensagem não segue o contrato: não é JSON, a versão é outra ou um campo
    está fora do formato. Não há o que repetir: o destino é a DLQ."""


@dataclass(frozen=True, slots=True)
class Sender:
    """Quem enviou o lote: o usuário do token, nunca o campo ``user`` do payload."""

    user_id: int


@dataclass(frozen=True, slots=True)
class TelemetryRecordV1:
    record_id: UUID
    vin: str
    vehicle_id: int
    sender_relation: str
    collected_at: datetime
    #: O ``user`` do registro bruto é o login de quem enviou? Só informa: a posse
    #: vem do token, e a divergência não reprova nada.
    payload_user_matches_sender: bool
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TelemetryBatchV1:
    schema_version: ClassVar[int] = SCHEMA_VERSION

    message_id: UUID
    received_at: datetime
    request_id: str | None
    sender: Sender
    records: tuple[TelemetryRecordV1, ...]


class _SenderSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    user_id = fields.Integer(required=True, strict=True)

    @post_load
    def _make(self, data: dict[str, Any], **_kwargs: Any) -> Sender:
        return Sender(**data)


class _RecordSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    record_id = fields.UUID(required=True)
    vin = fields.String(required=True)
    vehicle_id = fields.Integer(required=True, strict=True)
    sender_relation = fields.String(required=True, validate=validate.OneOf(SENDER_RELATIONS))
    collected_at = fields.AwareDateTime(required=True)
    payload_user_matches_sender = fields.Boolean(required=True)
    raw = fields.Dict(required=True)

    @post_load
    def _make(self, data: dict[str, Any], **_kwargs: Any) -> TelemetryRecordV1:
        return TelemetryRecordV1(**data)


class _BatchSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    schema_version = fields.Integer(required=True, strict=True)
    message_id = fields.UUID(required=True)
    received_at = fields.AwareDateTime(required=True)
    request_id = fields.String(required=True, allow_none=True)
    sender = fields.Nested(_SenderSchema, required=True)
    records = fields.List(
        fields.Nested(_RecordSchema), required=True, validate=validate.Length(min=1)
    )

    @post_load
    def _make(self, data: dict[str, Any], **_kwargs: Any) -> TelemetryBatchV1:
        del data["schema_version"]
        return TelemetryBatchV1(**{**data, "records": tuple(data["records"])})


def encode(batch: TelemetryBatchV1) -> bytes:
    """O envelope em JSON UTF-8, o corpo da mensagem."""
    body = _BatchSchema().dump(batch)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(CONTENT_ENCODING)


def decode(body: bytes) -> TelemetryBatchV1:
    """O envelope de volta, ou ``ContractError``. Campos desconhecidos são ignorados."""
    try:
        data = json.loads(body.decode(CONTENT_ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("o corpo não é JSON em UTF-8") from error
    if not isinstance(data, dict):
        raise ContractError("o envelope não é um objeto JSON")

    # Antes do schema, para a versão errada não se confundir com campo inválido.
    # `type(...) is int` porque `True == 1` em Python.
    version = data.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ContractError(f"schema_version {version!r} não é a v{SCHEMA_VERSION}")

    try:
        batch: TelemetryBatchV1 = _BatchSchema().load(data)
    except ValidationError as error:
        raise ContractError(f"envelope fora do contrato: {error.messages}") from error
    return batch
