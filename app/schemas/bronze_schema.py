"""Schemas da leitura do bronze pela silver: a query, o cursor e a página.

O cursor é opaco para a silver: base64url (sem o ``=`` do fim) de um JSON
``{"v": 2, "t": <ingested_at em milissegundos>, "id": "<record_id>", "f": "<impressão
dos filtros>"}``. Ele é a representação HTTP de um ``BronzeCursor``, e por isso o codec
mora aqui; o serviço só conhece a posição e os filtros. Os milissegundos bastam: é a
precisão da data no BSON, então a posição volta exatamente como saiu.
"""

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID

from marshmallow import (
    Schema,
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)

from domain.metric import INVALID_METRIC_MESSAGE, is_valid_metric
from domain.moment import INVALID_MOMENT_MESSAGE, fits_in_utc
from domain.ports import NO_FILTERS, BronzeCursor, BronzeFilters, BronzePosition
from domain.vin import INVALID_VIN_MESSAGE, is_valid_vin, normalize_vin

#: Os limites da página.
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000

#: Quantas métricas uma leitura pode filtrar.
MAX_METRICS = 10

#: A versão do formato do cursor. Formato novo é versão nova, e a anterior continua
#: aceita enquanto a silver puder ter um cursor dela guardado. Versão desconhecida dá 422.
CURSOR_VERSION = 2
#: As chaves de cada versão aceita. O v1, de antes dos filtros, vale como a leitura sem
#: filtro.
_CURSOR_KEYS = {1: frozenset({"v", "t", "id"}), 2: frozenset({"v", "t", "id", "f"})}
INVALID_CURSOR_MESSAGE = "Cursor inválido."
OTHER_FILTER_MESSAGE = "O cursor pertence a outro filtro."

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


class ReceivedCursor(NamedTuple):
    """O que um cursor diz: a posição e a impressão dos filtros com que foi emitido."""

    position: BronzePosition
    fingerprint: str


def _epoch_ms(moment: datetime) -> int:
    return (moment - _EPOCH) // _MS


def filters_fingerprint(filters: BronzeFilters) -> str:
    """A impressão de um recorte: igual para o mesmo recorte, qualquer que seja o fuso em
    que as datas vieram. Não protege de quem forja o cursor, e não precisa: só impede
    continuar, por engano, com o cursor de outra leitura."""
    recorte = [
        filters.vin,
        list(filters.metrics),
        *(
            None if moment is None else _epoch_ms(moment)
            for moment in (filters.collected_from, filters.collected_to)
        ),
    ]
    return hashlib.sha256(json.dumps(recorte).encode("ascii")).hexdigest()[:16]


def encode_cursor(cursor: BronzeCursor) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "t": _epoch_ms(cursor.position.ingested_at),
        "id": str(cursor.position.record_id),
        "f": filters_fingerprint(cursor.filters),
    }
    texto = json.dumps(payload, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(texto).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> ReceivedCursor:
    """O que diz um cursor emitido por ``encode_cursor``. Levanta ``ValueError`` para
    qualquer outra coisa."""
    try:
        texto = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(texto)
    except (binascii.Error, ValueError) as error:  # inclui JSON e UTF-8 inválidos
        raise ValueError("cursor sem base64url e JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("cursor que não é objeto")
    versao = payload.get("v")
    # `type(...) is int`: o `True` do JSON passaria por um `isinstance`.
    if type(versao) is not int or versao not in _CURSOR_KEYS:
        raise ValueError("versão do cursor desconhecida")
    if payload.keys() != _CURSOR_KEYS[versao]:
        raise ValueError("cursor com outras chaves")
    instante, record_id = payload["t"], payload["id"]
    fingerprint = payload.get("f", filters_fingerprint(NO_FILTERS))
    if type(instante) is not int or instante < 0 or not isinstance(record_id, str):
        raise ValueError("cursor com tipos errados")
    if not isinstance(fingerprint, str):
        raise ValueError("cursor com tipos errados")
    try:
        position = BronzePosition(ingested_at=_EPOCH + instante * _MS, record_id=UUID(record_id))
    except (OverflowError, ValueError) as error:
        raise ValueError("cursor fora do intervalo") from error
    return ReceivedCursor(position=position, fingerprint=fingerprint)


class CursorField(fields.String):
    """O cursor opaco na API: sai de um ``BronzeCursor`` e entra como ``ReceivedCursor``."""

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


def _vin_valido(value: str) -> None:
    if not is_valid_vin(value):
        raise ValidationError(INVALID_VIN_MESSAGE)


def _metrica_valida(value: str) -> None:
    if not is_valid_metric(value):
        raise ValidationError(INVALID_METRIC_MESSAGE)


def _data_que_cabe_em_utc(value: datetime) -> None:
    if not fits_in_utc(value):
        raise ValidationError(INVALID_MOMENT_MESSAGE)


class BronzeQuerySchema(Schema):
    """Parâmetros da leitura, na query string. O ``load`` devolve ``after`` (a posição do
    cursor), ``ingested_from``, ``filters`` (um ``BronzeFilters``) e ``limit``."""

    cursor = CursorField(
        load_default=None,
        metadata={
            "description": "O `next_cursor` da resposta anterior, chamando com os mesmos "
            "filtros. Sem ele, a leitura começa do primeiro registro do bronze."
        },
    )
    ingested_from = fields.AwareDateTime(
        load_default=None,
        validate=_data_que_cabe_em_utc,
        metadata={"description": "Começa dos gravados a partir daqui. Só sem `cursor`."},
    )
    vin = fields.Str(
        load_default=None,
        validate=_vin_valido,
        metadata={"description": "Só os registros deste VIN; aceita minúsculas."},
    )
    metric = fields.List(
        fields.Str(validate=_metrica_valida),
        load_default=list,
        validate=validate.Length(max=MAX_METRICS),
        metadata={
            "description": f"Só os registros com leitura disponível de ao menos uma destas "
            f"métricas. Repita o parâmetro; até {MAX_METRICS}."
        },
    )
    collected_from = fields.AwareDateTime(
        load_default=None,
        validate=_data_que_cabe_em_utc,
        metadata={
            "description": "Coletados a partir daqui. Recorte grande: combine com `vin` ou "
            "`ingested_from`, porque a data de coleta não tem índice."
        },
    )
    collected_to = fields.AwareDateTime(
        load_default=None,
        validate=_data_que_cabe_em_utc,
        metadata={"description": "Coletados até aqui, inclusive."},
    )
    limit = fields.Int(
        load_default=DEFAULT_LIMIT,
        validate=validate.Range(min=1, max=MAX_LIMIT),
        metadata={"description": f"Registros por página (máximo {MAX_LIMIT})."},
    )

    @validates_schema
    def _periodo_de_coleta_em_ordem(self, data: dict[str, Any], **_kwargs: Any) -> None:
        inicio, fim = data["collected_from"], data["collected_to"]
        if inicio is not None and fim is not None and inicio > fim:
            raise ValidationError(
                "`collected_from` é posterior a `collected_to`.", "collected_from"
            )

    @validates_schema
    def _um_ponto_de_partida(self, data: dict[str, Any], **_kwargs: Any) -> None:
        if data["cursor"] is not None and data["ingested_from"] is not None:
            raise ValidationError("Use `ingested_from` só sem `cursor`.", "ingested_from")

    @post_load
    def _monta_a_leitura(self, data: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        # A forma canônica é aqui, e não no serviço: a impressão do cursor depende dela.
        vin = None if data["vin"] is None else normalize_vin(data["vin"])
        filters = BronzeFilters(
            vin=vin,
            metrics=tuple(sorted(set(data["metric"]))),
            collected_from=data["collected_from"],
            collected_to=data["collected_to"],
        )
        received: ReceivedCursor | None = data["cursor"]
        if received is not None and received.fingerprint != filters_fingerprint(filters):
            raise ValidationError(OTHER_FILTER_MESSAGE, "cursor")
        return {
            "after": None if received is None else received.position,
            "ingested_from": data["ingested_from"],
            "filters": filters,
            "limit": data["limit"],
        }


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
            "description": "De onde continuar, com os mesmos filtros. Guarde-o só depois de "
            "processar a página. Na página vazia, é o ponto de onde a leitura partiu."
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
