"""Schemas do envio de telemetria: o lote que o app manda e o recibo 202.

O formato reprova o lote inteiro (422, com o erro por campo): é quebra de contrato
do app. A posse do veículo é decidida registro a registro, no serviço, e volta no
recibo. Nos registros e nas leituras, campo desconhecido é aceito e chega intacto
ao bronze, em ``raw``: o app pode ganhar campos sem quebrar a ingestão. Na raiz do
corpo, não. Os tipos são estritos: um ``"true"`` ou um ``"1065.0"`` é bug do app, e
não um valor a converter, porque o bruto seguiria com o tipo errado.
"""

from typing import Any

from marshmallow import (
    EXCLUDE,
    Schema,
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)

from domain.metric import INVALID_METRIC_MESSAGE, is_valid_metric
from domain.vin import INVALID_VIN_MESSAGE, is_valid_vin

#: Os limites do lote. Cerca de 8 minutos de leituras a 1 Hz por lote; são valores
#: iniciais, a calibrar com o volume real.
REGISTROS = validate.Length(min=1, max=500)
LEITURAS = validate.Length(min=1, max=64)
UNIDADE = validate.Length(max=16)


class _StrictBoolean(fields.Boolean):
    """Só ``true`` e ``false``: o ``Boolean`` aceitaria ``"true"``, ``1`` e ``"sim"``."""

    def _deserialize(self, value: Any, attr: str | None, data: Any, **kwargs: Any) -> bool | None:
        if not isinstance(value, bool):
            raise self.make_error("invalid", input=value)
        return super()._deserialize(value, attr, data, **kwargs)


class _StrictNumber(fields.Float):
    """Só número do JSON: o ``Float`` aceitaria ``"1065.0"``. Booleano não é número, e
    NaN e infinito o próprio ``Float`` recusa."""

    def _deserialize(self, value: Any, attr: str | None, data: Any, **kwargs: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self.make_error("invalid", input=value)
        return super()._deserialize(value, attr, data, **kwargs)


def _vin_valido(value: str) -> None:
    # Só a regra: quem leva o VIN à forma canônica é o serviço.
    if not is_valid_vin(value):
        raise ValidationError(INVALID_VIN_MESSAGE)


def _metrica_valida(value: str) -> None:
    if not is_valid_metric(value):
        raise ValidationError(INVALID_METRIC_MESSAGE)


class ReadingSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    metric = fields.Str(required=True, validate=_metrica_valida)
    unit = fields.Str(required=True, validate=UNIDADE)
    available = _StrictBoolean(required=True)
    value = _StrictNumber(required=True, allow_none=True)

    @validates_schema
    def _valor_acompanha_a_disponibilidade(self, data: dict[str, Any], **_kwargs: Any) -> None:
        if data["available"] != (data["value"] is not None):
            raise ValidationError(
                "`value` é numérico quando `available` é verdadeiro, e nulo quando é falso.",
                "value",
            )


class TelemetryRecordSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    id = fields.UUID(
        required=True,
        metadata={"description": "Gerado pelo app. É a chave de idempotência no bronze."},
    )
    vin = fields.Str(
        required=True,
        validate=_vin_valido,
        metadata={"description": "VIN (ISO 3779); aceita minúsculas."},
    )
    collected_at = fields.AwareDateTime(
        required=True, metadata={"description": "ISO 8601 com fuso horário."}
    )
    readings = fields.List(fields.Nested(ReadingSchema), required=True, validate=LEITURAS)

    @validates_schema
    def _metricas_sem_repeticao(self, data: dict[str, Any], **_kwargs: Any) -> None:
        metricas = [leitura["metric"] for leitura in data["readings"]]
        if len(set(metricas)) != len(metricas):
            raise ValidationError("Há métricas repetidas no registro.", "readings")

    @post_load(pass_original=True)
    def _guarda_o_original(
        self, data: dict[str, Any], original: dict[str, Any], **_kwargs: Any
    ) -> dict[str, Any]:
        # O bronze guarda o registro como chegou, com os campos que este schema
        # ignora. O campo `user`, se vier, fica só aqui: nunca autoriza nada.
        data["raw"] = original
        return data


class TelemetryBatchSchema(Schema):
    records = fields.List(
        fields.Nested(TelemetryRecordSchema),
        required=True,
        validate=REGISTROS,
        metadata={"description": "De 1 a 500 registros, com ids distintos."},
    )

    @validates_schema
    def _ids_sem_repeticao(self, data: dict[str, Any], **_kwargs: Any) -> None:
        ids = [registro["id"] for registro in data["records"]]
        if len(set(ids)) != len(ids):
            raise ValidationError("Há registros com o mesmo id no lote.", "records")


class RejectedRecordSchema(Schema):
    index = fields.Int(metadata={"description": "Posição do registro no lote, a partir de 0."})
    id = fields.UUID()
    vin = fields.Str()
    reason = fields.Str(
        metadata={"description": "`vehicle_not_authorized`: o remetente não tem vínculo com o VIN."}
    )


class BatchReceiptSchema(Schema):
    """O recibo do lote. Espelha ``service.telemetry.ingest_service.IngestReceipt``."""

    batch_id = fields.UUID(
        allow_none=True,
        metadata={
            "description": "Id do envelope publicado; nulo quando nenhum registro foi aceito."
        },
    )
    received_at = fields.DateTime()
    accepted = fields.Int(metadata={"description": "Registros publicados."})
    rejected = fields.List(
        fields.Nested(RejectedRecordSchema),
        metadata={"description": "Registros não publicados; o app não deve reenviá-los iguais."},
    )
