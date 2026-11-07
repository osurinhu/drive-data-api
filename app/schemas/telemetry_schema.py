"""Schemas do envio de telemetria: o lote que o app manda e o recibo 202.

O formato reprova o lote inteiro (422, com o erro por campo): é quebra de contrato
do app. A posse do veículo é decidida registro a registro, no serviço, e volta no
recibo. Nos registros e nas leituras, campo desconhecido é aceito e chega intacto
ao bronze, em ``raw``: o app pode ganhar campos sem quebrar a ingestão. Na raiz do
corpo, não.
"""

from typing import Any

from marshmallow import EXCLUDE, Schema, ValidationError, fields, post_load, validate

from domain.vin import INVALID_VIN_MESSAGE, is_valid_vin

#: Os limites do lote. Cerca de 8 minutos de leituras a 1 Hz por lote; são valores
#: iniciais, a calibrar com o volume real.
REGISTROS = validate.Length(min=1, max=500)
LEITURAS = validate.Length(min=1, max=64)
METRICA = validate.Regexp(
    r"^[a-z][a-z0-9_]{0,63}$",
    error="Use letras minúsculas, dígitos e _, começando por letra (até 64).",
)
UNIDADE = validate.Length(max=16)


def _vin_valido(value: str) -> None:
    # Só a regra: quem leva o VIN à forma canônica é o serviço.
    if not is_valid_vin(value):
        raise ValidationError(INVALID_VIN_MESSAGE)


class ReadingSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    metric = fields.Str(required=True, validate=METRICA)
    unit = fields.Str(required=True, validate=UNIDADE)
    available = fields.Bool(required=True)
    value = fields.Float(required=True, allow_none=True)


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
        metadata={"description": "De 1 a 500 registros."},
    )


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
