"""Schemas da entidade de exemplo.

Padrão a replicar em entidades novas: um schema de saída, um de criação, um de
atualização parcial, um de filtros de consulta e um de página.
"""

from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from schemas.pagination_schema import PageSchema, PaginationQuerySchema

#: Os limites ficam aqui, uma vez, porque valem na criação e na atualização.
#: Precisam acompanhar o `String(...)` da coluna: divergir dá DataError (500) em
#: vez de 422, e a coluna não pode ser importada daqui — `schema` não importa
#: nada do projeto.
NOME = validate.Length(min=1, max=100)
DESCRICAO = validate.Length(max=500)


class ItemSchema(Schema):
    """Representação de saída."""

    id = fields.Int()
    name = fields.Str()
    description = fields.Str(allow_none=True)
    owner_id = fields.Int()
    created_at = fields.DateTime()
    last_update = fields.DateTime()


class ItemCreateSchema(Schema):
    name = fields.Str(required=True, validate=NOME)
    description = fields.Str(load_default=None, allow_none=True, validate=DESCRICAO)


class ItemUpdateSchema(Schema):
    """Atualização parcial: todo campo é opcional, mas pelo menos um é exigido."""

    name = fields.Str(validate=NOME)
    description = fields.Str(allow_none=True, validate=DESCRICAO)

    @validates_schema
    def _require_at_least_one_field(self, data: dict, **_kwargs) -> None:
        if not data:
            raise ValidationError("Informe ao menos um campo para atualizar.")


class ItemQuerySchema(PaginationQuerySchema):
    name = fields.Str(
        load_default=None,
        metadata={"description": "Filtro por nome (parcial, sem diferenciar maiúsculas)."},
    )


class ItemPageSchema(PageSchema):
    items = fields.List(fields.Nested(ItemSchema))
