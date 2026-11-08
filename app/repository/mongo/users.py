"""Os usuários do bronze, um por processo, aplicados pelo ``flask mongo setup``.

O consumer grava e lê (``readWrite``), e a silver só lê (``read``). Os dois moram no
próprio banco do bronze, que é o ``authSource`` da URI deles, e não alcançam outro. A
credencial de administração fica só com o job ``setup``.

Aplicar de novo é o que troca a senha: o usuário existente recebe a senha e o papel
declarados aqui. É por isso que eles nascem no ``setup``, e não num script de
inicialização do Mongo, que só roda com o volume vazio.
"""

from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure

WRITER = "bronze_writer"
READER = "bronze_reader"

_USER_ALREADY_EXISTS = 51003


def apply(
    database: Database[dict[str, Any]], *, writer_password: str, reader_password: str
) -> None:
    """Cria os usuários, ou atualiza a senha e o papel dos que já existem."""
    _put_user(database, WRITER, writer_password, role="readWrite")
    _put_user(database, READER, reader_password, role="read")


def _put_user(database: Database[dict[str, Any]], name: str, password: str, *, role: str) -> None:
    roles = [{"role": role, "db": database.name}]
    try:
        database.command("createUser", name, pwd=password, roles=roles)
    except OperationFailure as error:
        if error.code != _USER_ALREADY_EXISTS:
            raise
        database.command("updateUser", name, pwd=password, roles=roles)
