"""Schema da sonda de disponibilidade."""

from marshmallow import Schema, fields


class HealthSchema(Schema):
    status = fields.Str(metadata={"description": "'ok' ou 'degraded'."})
    database = fields.Str(metadata={"description": "'ok' ou 'unreachable'."})
