"""Paginação: parâmetros de consulta e envelope de resposta.

O envelope vai no corpo da resposta (e não em header), seguindo o formato dos
projetos de origem. Os limites são definidos aqui, que é o único lugar onde a
paginação é validada.
"""

from marshmallow import Schema, fields, validate

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class PaginationQuerySchema(Schema):
    """Parâmetros de paginação aceitos na query string."""

    page = fields.Int(
        load_default=1,
        validate=validate.Range(min=1),
        metadata={"description": "Página desejada, começando em 1."},
    )
    per_page = fields.Int(
        load_default=DEFAULT_PAGE_SIZE,
        validate=validate.Range(min=1, max=MAX_PAGE_SIZE),
        metadata={"description": f"Itens por página (máximo {MAX_PAGE_SIZE})."},
    )


class PageSchema(Schema):
    """Metadados do envelope. Subclasses acrescentam o campo ``items``."""

    current_page = fields.Int(metadata={"description": "Página retornada."})
    total_of_items = fields.Int(metadata={"description": "Total de registros no filtro."})
    total_of_pages = fields.Int(metadata={"description": "Total de páginas disponíveis."})
    has_prev = fields.Bool(metadata={"description": "Existe página anterior."})
    has_next = fields.Bool(metadata={"description": "Existe página seguinte."})
