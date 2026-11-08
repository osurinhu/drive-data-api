"""Portas do pipeline de telemetria: interfaces (``Protocol``) e os tipos que elas trocam.

Folha do domínio: só importa outras folhas (o contrato da fila), nenhuma biblioteca de
infraestrutura. Os serviços do pipeline dependem destas interfaces, e os adaptadores
(repositórios, mensageria) as implementam sem herdar delas: ``Protocol`` é estrutural.
Assim a regra se testa com dublês, e o processo que usa a regra não carrega o ORM.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from domain.contracts.telemetry_batch import TelemetryBatchV1


class SenderRelation(StrEnum):
    """O vínculo que autoriza um usuário a enviar dados de um veículo."""

    OWNER = "owner"  # o gestor: quem cadastrou o veículo
    DRIVER = "driver"  # motorista ativo do veículo


@dataclass(frozen=True, slots=True)
class AuthorizedVehicle:
    """Veículo pelo qual o usuário pode enviar, e com que vínculo."""

    vehicle_id: int
    relation: SenderRelation


class VehicleAuthorization(Protocol):
    """A posse que o producer consulta antes de publicar."""

    def authorized_vins(self, user_id: int, vins: Collection[str]) -> dict[str, AuthorizedVehicle]:
        """Dos VINs informados, já na forma canônica (``domain.vin.normalize_vin``), os
        de veículos ativos pelos quais o usuário pode enviar, com o vínculo. Os
        demais ficam fora do dicionário."""
        ...


class PublishFailed(Exception):
    """O broker não confirmou a publicação: fora do ar, sem fila para a routing key ou
    recusando (fila cheia).

    Quem chama trata a mensagem como não publicada. Se ela tiver chegado mesmo
    assim (o prazo esgotou depois da entrega), o reenvio é seguro: o consumer
    grava cada registro pelo id, uma vez só.
    """


class TelemetryPublisher(Protocol):
    """A fila que leva os lotes aceitos ao bronze."""

    def publish(self, batch: TelemetryBatchV1) -> None:
        """Publica o envelope e só volta depois da confirmação do broker. Levanta
        ``PublishFailed`` quando ela não vem."""
        ...


@dataclass(frozen=True, slots=True)
class BronzeEntry:
    """Um registro aceito, como o bronze o guarda: o bruto e os metadados da ingestão.

    É o que o serviço do consumer monta; o layout do documento (BSON) é do
    adaptador, que o traduz.
    """

    record_id: UUID
    vin: str
    vehicle_id: int
    user_id: int
    sender_relation: str
    payload_user_matches_sender: bool
    collected_at: datetime
    #: As métricas com leitura disponível no registro, ordenadas e sem repetição.
    available_metrics: tuple[str, ...]
    received_at: datetime
    #: O relógio do servidor do bronze no momento da gravação.
    ingested_at: datetime
    message_id: UUID
    request_id: str | None
    schema_version: int
    #: O registro exatamente como o app o enviou.
    raw: dict[str, Any]


class BronzeUnavailable(Exception):
    """O bronze não respondeu (servidor fora, eleição, rede). É transitório: a mensagem
    volta para a fila e a gravação se repete, o que é seguro porque ela é idempotente."""


class BronzeWriter(Protocol):
    """O bronze, do lado de quem grava (o consumer)."""

    def server_time(self) -> datetime:
        """A hora do servidor do bronze, com fuso. Levanta ``BronzeUnavailable``."""
        ...

    def insert_new(self, entries: Sequence[BronzeEntry]) -> set[UUID]:
        """Grava os registros que ainda não estão no bronze e devolve os ids dos que já
        estavam (``record_id``), que ficam como estão. Levanta ``BronzeUnavailable``."""
        ...
