"""Schemas de veículos (os cinco da receita, no molde de `item_schema.py`)."""

from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from schemas.pagination_schema import PageSchema, PaginationQuerySchema
from vin import INVALID_VIN_MESSAGE, is_valid_vin

#: Acompanha o `String(50)` da coluna. O VIN não tem limite de tamanho aqui: o
#: formato já fixa 17 caracteres depois da normalização.
APELIDO = validate.Length(max=50)


def _vin_valido(value: str) -> None:
    # Só a regra: quem grava a forma canônica (maiúsculas, sem espaços nas pontas)
    # é o serviço, que atende também quem não passa por schema.
    if not is_valid_vin(value):
        raise ValidationError(INVALID_VIN_MESSAGE)


class VehicleSchema(Schema):
    """Representação de saída."""

    id = fields.Int()
    vin = fields.Str()
    nickname = fields.Str(allow_none=True)
    owner_id = fields.Int(metadata={"description": "Id do gestor do veículo: quem o cadastrou."})
    created_at = fields.DateTime()
    last_update = fields.DateTime()


class VehicleCreateSchema(Schema):
    vin = fields.Str(
        required=True,
        validate=_vin_valido,
        metadata={
            "description": "VIN (ISO 3779): 17 letras e dígitos, sem I, O e Q. "
            "Aceita minúsculas; é gravado em maiúsculas."
        },
    )
    nickname = fields.Str(
        load_default=None,
        allow_none=True,
        validate=APELIDO,
        metadata={"description": "Apelido, para o gestor reconhecer o veículo."},
    )


class VehicleUpdateSchema(Schema):
    """Atualização parcial. O VIN fica de fora de propósito: ele identifica o
    veículo e não muda (campo desconhecido responde 422)."""

    nickname = fields.Str(allow_none=True, validate=APELIDO)

    @validates_schema
    def _require_at_least_one_field(self, data: dict, **_kwargs) -> None:
        if not data:
            raise ValidationError("Informe ao menos um campo para atualizar.")


class VehicleQuerySchema(PaginationQuerySchema):
    vin = fields.Str(
        load_default=None,
        metadata={"description": "Filtro por VIN (parcial, sem diferenciar maiúsculas)."},
    )


class VehiclePageSchema(PageSchema):
    items = fields.List(fields.Nested(VehicleSchema))
