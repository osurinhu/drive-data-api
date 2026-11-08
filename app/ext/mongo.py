"""Cliente do MongoDB, a camada bronze: um por processo, sem conexão até o primeiro uso.

É dono do ``MongoClient`` como ``ext.database`` é dono do ``db``: os adaptadores
de ``repository/mongo/`` pedem o banco daqui (``mongo.db``), e só esta extensão
conhece a URI.

O cliente nasce na subida com ``connect=False``. O PyMongo já valida a URI e as
opções, mas não abre conexão nem thread, nem consulta o DNS, até a primeira
operação: montar a aplicação não conecta a nada, e os comandos que não usam o
Mongo (``flask create-user``) não abrem conexão. Um ``MongoClient`` não atravessa
fork, porque o pool e as threads de monitoramento ficam no processo que o abriu.
Por isso o processo que não é o que criou o cliente cria o seu (o gunicorn com
``--preload``, por exemplo, monta a aplicação antes do fork dos workers).

Também registra o grupo ``flask mongo``, com a operação do bronze (os índices, a retenção
e os usuários por processo).
"""

import logging
import os
import threading
from typing import Any

import click
import pymongo
from flask import Flask, current_app
from flask.cli import AppGroup
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

from instrumentation.probes import register_probe
from repository.mongo import indexes, users

logger = logging.getLogger(__name__)

Document = dict[str, Any]

_EXTENSION_KEY = "mongo"
_SCHEMES = ("mongodb://", "mongodb+srv://")


class MongoConnection:
    """A configuração validada e o cliente do processo corrente.

    Levanta ``ValueError`` com mensagem própria se o PyMongo recusar a URI: a
    mensagem dele pode repetir pedaços da senha (uma senha com "/" sem
    codificação vira caminho na URI, e o erro cita o caminho).
    """

    def __init__(
        self, uri: str, database: str, *, app_name: str, timeout: float, probe_timeout: float
    ) -> None:
        self._uri = uri
        self._database = database
        self._app_name = app_name
        self._timeout_ms = int(timeout * 1000)
        self._probe_timeout = probe_timeout
        self._lock = threading.Lock()
        try:
            self._client = self._new_client()
        except (ValueError, PyMongoError):
            raise ValueError(
                "o PyMongo recusou a URI (confira host, porta e opções; usuário e "
                "senha com caracteres como / ? # @ precisam de codificação)"
            ) from None
        self._pid = os.getpid()

    def _new_client(self) -> MongoClient[Document]:
        return MongoClient(
            self._uri,
            # Sem conexão, thread nem DNS até a primeira operação.
            connect=False,
            appname=self._app_name,
            serverSelectionTimeoutMS=self._timeout_ms,
            connectTimeoutMS=self._timeout_ms,
            # Datas voltam com fuso (UTC), como as do Postgres (TIMESTAMPTZ).
            tz_aware=True,
        )

    @property
    def client(self) -> MongoClient[Document]:
        # O pid é o do processo que criou o cliente. Depois de um fork, o filho
        # cria o seu e abandona o herdado sem fechá-lo: fechar no filho mexeria
        # em sockets e locks que ainda são do pai.
        if self._pid != os.getpid():
            with self._lock:
                if self._pid != os.getpid():
                    self._client = self._new_client()
                    self._pid = os.getpid()
        return self._client

    @property
    def database(self) -> Database[Document]:
        return self.client[self._database]

    def ping(self) -> bool:
        """Sonda do health: o servidor responde ao ``ping`` dentro do prazo."""
        try:
            with pymongo.timeout(self._probe_timeout):
                self.client.admin.command("ping")
        except PyMongoError as error:
            # %.300s: a mensagem do pymongo traz a descrição inteira da topologia.
            logger.warning("Sonda do Mongo: sem resposta (%s: %.300s)", type(error).__name__, error)
            return False
        return True


class Mongo:
    """Acesso ao Mongo da aplicação corrente, no molde do ``db`` de ``ext.database``."""

    @property
    def client(self) -> MongoClient[Document]:
        return _connection().client

    @property
    def db(self) -> Database[Document]:
        return _connection().database


mongo = Mongo()


def _connection() -> MongoConnection:
    connection: MongoConnection = current_app.extensions[_EXTENSION_KEY]
    return connection


mongo_cli = AppGroup("mongo", help="Operação do MongoDB (o bronze).")


@mongo_cli.command("setup")
def setup_command() -> None:
    """Aplica os índices, a retenção e os usuários do bronze (idempotente).

    É o que o job `setup` roda, depois das migrações e da topologia do RabbitMQ. A
    MONGO_URI dele é a de administração; a dos outros processos é a do usuário de cada um.
    """
    config = current_app.config
    writer_password = _required_password("MONGO_WRITER_PASSWORD")
    reader_password = _required_password("MONGO_READER_PASSWORD")
    database = mongo.db
    try:
        nomes = indexes.apply(
            database,
            bronze_retention_days=int(config["BRONZE_RETENTION_DAYS"]),
            rejections_retention_days=int(config["REJECTIONS_RETENTION_DAYS"]),
        )
        users.apply(database, writer_password=writer_password, reader_password=reader_password)
    except ValueError as error:
        raise click.ClickException(str(error)) from None
    except PyMongoError as error:
        # %.300s: a mensagem do pymongo traz a topologia inteira, mas não a senha.
        raise click.ClickException(
            f"o Mongo recusou ou não respondeu ({type(error).__name__}: {str(error):.300})"
        ) from error
    click.echo(f"Índices do bronze aplicados no banco '{database.name}': {', '.join(nomes)}.")
    click.echo(f"Usuários do bronze aplicados: {users.WRITER}, {users.READER}.")


def _required_password(name: str) -> str:
    password = str(current_app.config.get(name) or "")
    if not password:
        raise click.ClickException(
            f"{name} não definida. Preencha FLASK_{name}, ou rode via docker compose, "
            "no serviço setup."
        )
    return password


def init_app(app: Flask) -> None:
    uri = str(app.config.get("MONGO_URI") or "")
    if not uri.startswith(_SCHEMES):
        # A mensagem nunca repete a URI: ela leva a senha.
        raise RuntimeError(
            "MONGO_URI não definida ou sem o esquema mongodb:// (ou mongodb+srv://). "
            "Preencha FLASK_MONGO_URI no .env, ou rode via docker compose, que monta a URI."
        )
    database = str(app.config.get("MONGO_DATABASE") or "")
    if not database:
        raise RuntimeError("MONGO_DATABASE não definido: ele fica no settings.toml.")

    try:
        connection = MongoConnection(
            uri,
            database,
            app_name=f"drive-data:{app.config.get('PROCESS') or 'default'}",
            timeout=float(app.config["MONGO_TIMEOUT_SECONDS"]),
            probe_timeout=float(app.config["HEALTH_PROBE_TIMEOUT_SECONDS"]),
        )
    except ValueError as error:
        # A mensagem de MongoConnection é fixa; o `from None` tira do traceback
        # o erro original do PyMongo, que pode trazer pedaços da senha.
        raise RuntimeError(f"MONGO_URI inválida: {error}.") from None
    app.extensions[_EXTENSION_KEY] = connection
    register_probe(app, "mongo", connection.ping)
    app.cli.add_command(mongo_cli)
