"""Envelope de paginação.

Dataclass, e não dicionário: as mesmas chaves existem em
``schemas.pagination_schema.PageSchema``, e ``tests/test_contracts.py`` reprova
se os dois divergirem. O marshmallow serializa por acesso a atributo, então a
rota não muda em nada.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class Page[T]:
    items: list[T]
    current_page: int
    total_of_items: int
    total_of_pages: int
    has_prev: bool
    has_next: bool
