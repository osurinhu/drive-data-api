"""Leitura do bronze pela silver (bronze-api): a janela de estabilização e o cursor."""

from datetime import datetime, timedelta

from domain.authz import OwnerScope
from domain.ports import BRONZE_START, BronzeEntry, BronzePosition, BronzeReader, BronzeUnavailable
from service.errors import ServiceUnavailableError
from service.pagination import CursorPage

UNAVAILABLE_MESSAGE = "O bronze está temporariamente indisponível. Tente novamente em instantes."


class BronzeReadService:
    """Entrega o bronze à silver em ordem de gravação, sem perder nem repetir registro.

    Não repete porque a posição ``(ingested_at, record_id)`` é única e a leitura
    começa estritamente depois dela. Não perde porque só sai o que foi gravado há
    mais de ``settle_seconds``, pelo relógio do próprio bronze, que é o mesmo que
    carimba o ``ingested_at``: nenhum registro recebe um carimbo abaixo desse limite
    depois da leitura, desde que entre o consumer carimbar e a gravação ficar
    visível passe menos que a janela (02-decisoes, "A silver lê o bronze por cursor").
    """

    def __init__(self, reader: BronzeReader, *, settle_seconds: float) -> None:
        self._reader = reader
        self._settle = timedelta(seconds=settle_seconds)

    def read(
        self, owner_scope: OwnerScope, *, after: BronzePosition | None, limit: int
    ) -> CursorPage[BronzeEntry, BronzePosition]:
        """Até ``limit`` registros depois de ``after`` (sem ele, do começo do bronze).

        ``has_more`` vem de pedir um registro a mais. Na página vazia, o
        ``next_cursor`` é a posição recebida: a silver nunca fica sem cursor. Levanta
        ``ServiceUnavailableError`` quando o bronze não responde.
        """
        start = after if after is not None else BRONZE_START
        try:
            watermark: datetime = self._reader.server_time() - self._settle
            entries = self._reader.read_after(
                start, until=watermark, limit=limit + 1, owner_scope=owner_scope
            )
        except BronzeUnavailable as error:
            # Sem Retry-After: a silver repete no próprio intervalo de polling, com o
            # mesmo cursor.
            raise ServiceUnavailableError(UNAVAILABLE_MESSAGE) from error

        items = entries[:limit]
        next_cursor = (
            BronzePosition(ingested_at=items[-1].ingested_at, record_id=items[-1].record_id)
            if items
            else start
        )
        return CursorPage(
            items=items, next_cursor=next_cursor, has_more=len(entries) > limit, watermark=watermark
        )
