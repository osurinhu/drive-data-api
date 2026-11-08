"""Schemas dos motoristas: o vínculo visto pelo gestor e visto pelo motorista.

O vínculo não se altera (só nasce e termina), então não há schema de PATCH; as listas
só paginam, e usam o `PaginationQuerySchema` como está.
"""

from marshmallow import Schema, fields, validate

from schemas.pagination_schema import PageSchema

#: Acompanha o `String(50)` de `users.name`.
LOGIN = validate.Length(min=1, max=50)


class DriverSchema(Schema):
    """O motorista de um veículo, como o gestor o vê."""

    vehicle_id = fields.Int()
    driver_id = fields.Int(
        metadata={"description": "Id do usuário motorista (o mesmo da rota de remoção)."}
    )
    driver_name = fields.Str(attribute="driver.name", metadata={"description": "Login dele."})
    created_at = fields.DateTime(metadata={"description": "Desde quando ele é motorista."})


class DriverCreateSchema(Schema):
    user_name = fields.Str(
        required=True,
        validate=LOGIN,
        metadata={"description": "Login da pessoa que vai dirigir e enviar dados do veículo."},
    )


class DriverPageSchema(PageSchema):
    items = fields.List(fields.Nested(DriverSchema))


class DrivingSchema(Schema):
    """Um veículo que o usuário dirige, como o motorista o vê."""

    vehicle_id = fields.Int()
    vin = fields.Str(attribute="vehicle.vin")
    nickname = fields.Str(
        attribute="vehicle.nickname",
        allow_none=True,
        metadata={"description": "O apelido que o gestor deu ao veículo."},
    )
    owner_name = fields.Str(
        attribute="vehicle.owner.name",
        metadata={"description": "Login do gestor, que o adicionou como motorista."},
    )
    created_at = fields.DateTime(metadata={"description": "Desde quando você é motorista dele."})


class DrivingPageSchema(PageSchema):
    items = fields.List(fields.Nested(DrivingSchema))
