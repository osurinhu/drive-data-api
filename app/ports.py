"""Portas do pipeline de telemetria: interfaces (``Protocol``) e os tipos que elas trocam.

Módulo folha: não importa nada do projeto nem biblioteca de infraestrutura. Os
serviços do pipeline dependem destas interfaces, e os adaptadores (repositórios,
mensageria) as implementam sem herdar delas: ``Protocol`` é estrutural. Assim a
regra se testa com dublês, e o processo que usa a regra não carrega o ORM.
"""

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


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
        """Dos VINs informados, já na forma canônica (``vin.normalize_vin``), os
        de veículos ativos pelos quais o usuário pode enviar, com o vínculo. Os
        demais ficam fora do dicionário."""
        ...
