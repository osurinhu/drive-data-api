"""Schemas das sondas de disponibilidade."""

from marshmallow import Schema, fields

_ESTADO = "'ok' ou 'unreachable'."
_SO_QUEM_USA = "Só nos processos que o usam."


class HealthSchema(Schema):
    """Uma chave por dependência carregada no processo; a de quem não a usa não aparece."""

    status = fields.Str(
        metadata={"description": "'ok' com todas as dependências respondendo; 'degraded' se não."}
    )
    database = fields.Str(metadata={"description": f"PostgreSQL: {_ESTADO}"})
    mongo = fields.Str(metadata={"description": f"MongoDB (bronze): {_ESTADO} {_SO_QUEM_USA}"})
    rabbitmq = fields.Str(metadata={"description": f"RabbitMQ: {_ESTADO} {_SO_QUEM_USA}"})


class LivenessSchema(Schema):
    status = fields.Str(metadata={"description": "Sempre 'ok': o processo está no ar."})
