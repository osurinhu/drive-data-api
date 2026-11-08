"""Envelopes de paginação: por número de página (o CRUD) e por cursor (a silver).

Dataclass, e não dicionário: as mesmas chaves existem no schema que serializa cada
um (``schemas.pagination_schema.PageSchema`` e ``schemas.bronze_schema.BronzePageSchema``),
e ``tests/test_contracts.py`` reprova se os dois divergirem. O marshmallow serializa
por acesso a atributo, então a rota não muda em nada.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Page[T]:
    items: list[T]
    current_page: int
    total_of_items: int
    total_of_pages: int
    has_prev: bool
    has_next: bool


@dataclass(slots=True)
class CursorPage[T, C]:
    """Uma página da leitura por cursor, sem contagem total: a coleção cresce enquanto
    é lida, e um total não teria significado estável (nem custo baixo)."""

    items: list[T]
    #: De onde a próxima chamada continua. Nunca falta: na página vazia, é a posição
    #: recebida.
    next_cursor: C
    #: Há mais itens já disponíveis: a próxima chamada pode vir sem espera.
    has_more: bool
    #: O limite superior desta leitura. O que for posterior a ele aparece nas próximas.
    watermark: datetime
