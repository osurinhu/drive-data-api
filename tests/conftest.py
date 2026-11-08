"""Fixtures da suíte.

Os testes rodam contra PostgreSQL de verdade (o mesmo dialeto de produção), e
não SQLite: índices parciais, `ilike`, TIMESTAMPTZ e defaults de servidor se
comportam de forma diferente entre dialetos, e justamente esses detalhes é que
precisam de cobertura.

Configure o banco de testes em `FLASK_TEST_DATABASE_URI`, e o Mongo e o RabbitMQ
em `FLASK_TEST_MONGO_URI`, `FLASK_TEST_AMQP_URL` e `FLASK_TEST_AMQP_MANAGEMENT_URL`
(ver .env.example).

As pastas separam os testes pela infraestrutura que exigem: a raiz usa Postgres e
HTTP; `unit/` não usa nada; `mongo/` e `rabbitmq/` usam o serviço do nome; `e2e/`
usa os três. Cada uma tem o seu `conftest.py`. As fixtures que servem a mais de
uma pasta ficam aqui, sem autouse, para que nenhum teste pague por
infraestrutura que não usa.
"""

import contextlib
import os
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pika
import pytest
from flask import Flask
from sqlalchemy import event

from domain.authz import ANY_OWNER, Role
from ext.config import PROCESS_ENVVAR, build_config

DEFAULT_TEST_DATABASE_URI = "postgresql+psycopg://baseapi:baseapi@localhost:5432/baseapi_test"
DEFAULT_TEST_MONGO_URI = "mongodb://root:root@localhost:27017/?authSource=admin"
DEFAULT_TEST_AMQP_URL = "amqp://admin:admin@localhost:5672/drive-data-test"
DEFAULT_TEST_AMQP_MANAGEMENT_URL = "http://admin:admin@localhost:15672"
DEFAULT_PASSWORD = "senha-de-teste-123"

# A suíte roda sempre o perfil [default], que carrega todas as extensões e rotas:
# um FLASK_PROCESS esquecido no ambiente ou no .env não pode tirar metade dela.
os.environ[PROCESS_ENVVAR] = "default"

# Carrega settings.toml + .env + variáveis de ambiente sem montar a aplicação —
# é a mesma função que o Alembic usa. Reaproveitá-la evita manter aqui um parser
# de .env escrito à mão.
_config = build_config()

# Atribuição (e não setdefault): mesmo que o .env aponte para o banco de
# desenvolvimento, a suíte precisa ir para o banco de testes — ela apaga tabelas.
# O mesmo vale para o Mongo (o banco ganha o sufixo _test, como o do Postgres) e
# para o RabbitMQ (o vhost de testes).
os.environ["FLASK_SQLALCHEMY_DATABASE_URI"] = (
    _config.get("TEST_DATABASE_URI") or DEFAULT_TEST_DATABASE_URI
)
os.environ["FLASK_MONGO_URI"] = _config.get("TEST_MONGO_URI") or DEFAULT_TEST_MONGO_URI
os.environ["FLASK_MONGO_DATABASE"] = f"{_config['MONGO_DATABASE']}_test"
os.environ["FLASK_AMQP_URL"] = _config.get("TEST_AMQP_URL") or DEFAULT_TEST_AMQP_URL
AMQP_MANAGEMENT_URL = _config.get("TEST_AMQP_MANAGEMENT_URL") or DEFAULT_TEST_AMQP_MANAGEMENT_URL

# Chaves fixas e descartáveis. Precisam ter 32 bytes ou mais: abaixo disso o
# PyJWT avisa que a chave é curta demais para HMAC-SHA256 (RFC 7518).
os.environ.setdefault("FLASK_SECRET_KEY", "chave-de-teste-sem-nenhum-valor-real-0001")
os.environ.setdefault("FLASK_JWT_SECRET_KEY", "chave-jwt-de-teste-sem-valor-real-0002")

from app import create_app  # noqa: E402
from domain.contracts.telemetry_batch import (  # noqa: E402
    EXCHANGE,
    ROUTING_KEY,
    Sender,
    TelemetryBatchV1,
    TelemetryRecordV1,
)
from domain.ports import BronzeEntry, BronzeUnavailable, PublishFailed  # noqa: E402
from ext import mongo as ext_mongo  # noqa: E402
from ext.database import db  # noqa: E402
from messaging import topology  # noqa: E402
from messaging.connection import connection_parameters  # noqa: E402
from messaging.management import ManagementApi  # noqa: E402


@pytest.fixture(scope="session")
def app():
    application = create_app()
    application.config.update(TESTING=True)

    with application.app_context():
        db.drop_all()
        db.create_all()

    yield application

    with application.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_tables(app):
    """Esvazia as tabelas após cada teste, para que a ordem não importe."""
    yield
    with app.app_context():
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()


@pytest.fixture
def make_user(app):
    """Cria um usuário no banco e devolve seus dados como dict.

    Devolve dict (e não o modelo) porque o objeto ORM ficaria desanexado da
    sessão ao sair do contexto da aplicação.
    """

    def _make(name: str = "usuario", password: str = DEFAULT_PASSWORD, role: Role = Role.USUARIO):
        with app.app_context():
            user = app.container.user_service().register(name=name, password=password, role=role)
            return {"id": user.id, "name": user.name, "password": password}

    return _make


@pytest.fixture
def login(client, make_user):
    """Cria um usuário, autentica e devolve (dados do usuário, headers)."""

    def _login(name: str = "usuario", password: str = DEFAULT_PASSWORD, role: Role = Role.USUARIO):
        user = make_user(name=name, password=password, role=role)
        response = client.post(
            "/api/v1/auth/token", json={"name": user["name"], "password": password}
        )
        assert response.status_code == 200, response.get_json()
        token = response.get_json()["access_token"]
        return user, {"Authorization": f"Bearer {token}"}

    return _login


@pytest.fixture
def headers(login):
    """Headers autenticados de um usuário comum — o caso mais frequente."""
    _, auth_headers = login()
    return auth_headers


@pytest.fixture
def set_role(app):
    """Troca o papel de um usuário já criado, sem reemitir o token.

    É o que permite testar que a autorização lê o banco, e não o token.
    """

    def _set(name: str, role: Role):
        with app.app_context():
            app.container.user_service().set_role(name, role)

    return _set


@pytest.fixture
def sql_log(app):
    """Registra todo SQL emitido, para assertar custo em consultas.

    O engine sobrevive ao contexto de aplicação, então o listener é instalado
    nele e vale para qualquer sessão criada depois.
    """
    with app.app_context():
        engine = db.engine

    statements: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


# --- Mongo e RabbitMQ ----------------------------------------------------------


@pytest.fixture(scope="session")
def _app_do_mongo():
    """Aplicação mínima, só com ``ext.mongo``. A aplicação inteira (``app``)
    recriaria as tabelas do Postgres, e os testes de Mongo não usam o Postgres."""
    aplicacao = Flask("testes-do-mongo")
    aplicacao.config.update(
        PROCESS="testes",
        MONGO_URI=os.environ["FLASK_MONGO_URI"],
        MONGO_DATABASE=os.environ["FLASK_MONGO_DATABASE"],
        MONGO_TIMEOUT_SECONDS=_config["MONGO_TIMEOUT_SECONDS"],
        HEALTH_PROBE_TIMEOUT_SECONDS=_config["HEALTH_PROBE_TIMEOUT_SECONDS"],
    )
    ext_mongo.init_app(aplicacao)
    yield aplicacao
    aplicacao.extensions["mongo"].client.close()


@pytest.fixture
def mongo_db(_app_do_mongo):
    """O banco de testes do Mongo (``<MONGO_DATABASE>_test``), para ler e preparar.

    Não abre contexto de aplicação: aberto durante o teste inteiro, ele viraria o
    ``current_app`` de tudo, inclusive dos comandos da CLI (o ``with_appcontext``
    reaproveita o contexto que encontra). Quem usa os adaptadores de
    ``repository/mongo`` abre o contexto (``tests/mongo/`` o abre para todos).
    """
    connection: ext_mongo.MongoConnection = _app_do_mongo.extensions["mongo"]
    return connection.database


@pytest.fixture
def bronze_limpo(mongo_db):
    """O banco de testes do Mongo, com as coleções (e os índices delas) apagadas no
    fim do teste. Autouse em ``tests/mongo/`` e ``tests/e2e/``."""
    yield mongo_db
    # A trava é o sufixo: a suíte apaga coleções, e o banco de desenvolvimento
    # nunca pode ser o alvo, nem por engano de configuração.
    assert mongo_db.name.endswith("_test"), mongo_db.name
    for colecao in mongo_db.list_collection_names():
        mongo_db.drop_collection(colecao)


@pytest.fixture(scope="session")
def amqp_url():
    """URL AMQP da suíte, já no vhost de testes."""
    return os.environ["FLASK_AMQP_URL"]


@pytest.fixture(scope="session")
def amqp_management_url():
    """URL da API de gerenciamento do RabbitMQ de testes, com a credencial de operação."""
    return AMQP_MANAGEMENT_URL


@pytest.fixture(scope="session")
def amqp_management(amqp_management_url):
    """Cliente da API de gerenciamento do RabbitMQ de testes."""
    return ManagementApi(amqp_management_url, timeout=5)


@pytest.fixture(scope="session")
def amqp_vhost(amqp_url, amqp_management):
    """Garante o vhost de testes e a permissão do usuário da URL de teste nele.

    O vhost não nasce sozinho como o banco do Mongo: é criado (ou mantido, porque
    o PUT é idempotente) pela API de gerenciamento, a mesma que a topologia usa.
    Devolve o nome do vhost.
    """
    parametros = connection_parameters(amqp_url, timeout=5, heartbeat=60, connection_name="pytest")
    vhost = parametros.virtual_host
    amqp_management.ensure_vhost(vhost)
    amqp_management.grant(
        vhost, parametros.credentials.username, configure=".*", write=".*", read=".*"
    )
    return vhost


@pytest.fixture(scope="session")
def delivery_limit():
    """O ``AMQP_DELIVERY_LIMIT`` do settings.toml, o que a policy de teste aplica."""
    return int(_config["AMQP_DELIVERY_LIMIT"])


@pytest.fixture(scope="session")
def queue_max_bytes():
    """O ``AMQP_QUEUE_MAX_BYTES`` do settings.toml, o que a policy de teste aplica."""
    return int(_config["AMQP_QUEUE_MAX_BYTES"])


@pytest.fixture(scope="session")
def amqp_topology(amqp_vhost, amqp_management, delivery_limit, queue_max_bytes):
    """A topologia da telemetria aplicada no vhost de testes, pelo mesmo
    ``topology.apply`` que o ``flask rabbitmq setup`` usa. Devolve o vhost."""
    topology.apply(
        amqp_management, amqp_vhost, delivery_limit=delivery_limit, max_bytes=queue_max_bytes
    )
    return amqp_vhost


def _conectar(amqp_url: str, vhost: str) -> pika.BlockingConnection:
    parametros = connection_parameters(amqp_url, timeout=5, heartbeat=60, connection_name="pytest")
    assert parametros.virtual_host == vhost, "a fila lida não é a da topologia aplicada"
    return pika.BlockingConnection(parametros)


@contextlib.contextmanager
def _fila_de_teste(amqp_url: str, vhost: str, fila: str):
    """A fila vazia no começo e no fim do teste, e a função que a esvazia, devolvendo
    as mensagens como pares ``(propriedades, corpo)``.

    ``drenar(minimo)`` espera até chegarem ``minimo`` mensagens, por até 5 s: o
    dead-letter é assíncrono, e a mensagem morta chega à DLQ um instante depois da
    recusa.
    """
    connection = _conectar(amqp_url, vhost)
    channel = connection.channel()
    channel.queue_purge(fila)

    def _drenar(minimo: int = 0) -> list[tuple[pika.BasicProperties, bytes]]:
        mensagens: list[tuple[pika.BasicProperties, bytes]] = []
        prazo = time.monotonic() + 5
        while True:
            method, properties, body = channel.basic_get(fila, auto_ack=True)
            if method is not None:
                mensagens.append((properties, body))
            elif len(mensagens) >= minimo or time.monotonic() >= prazo:
                return mensagens
            else:
                connection.sleep(0.1)

    try:
        yield _drenar
    finally:
        channel.queue_purge(fila)
        connection.close()


@pytest.fixture
def bronze_queue(amqp_url, amqp_topology):
    """A fila do bronze no vhost de testes, vazia no começo do teste. Devolve a
    função que a esvazia (ver ``_fila_de_teste``)."""
    with _fila_de_teste(amqp_url, amqp_topology, topology.QUEUE) as drenar:
        yield drenar


@pytest.fixture
def dead_letters(amqp_url, amqp_topology):
    """A DLQ no vhost de testes, vazia no começo do teste. Devolve a função que a
    esvazia (ver ``_fila_de_teste``)."""
    with _fila_de_teste(amqp_url, amqp_topology, topology.DEAD_LETTER_QUEUE) as drenar:
        yield drenar


@pytest.fixture
def publicar(amqp_url, amqp_topology):
    """Publica um corpo qualquer no endereço do contrato, com confirmação.

    Serve para a mensagem que o producer nunca mandaria (fora do contrato) e para o
    envelope válido (``encode(lote)``), sem passar pelo publisher.
    """
    connection = _conectar(amqp_url, amqp_topology)
    channel = connection.channel()
    channel.confirm_delivery()

    def _publicar(corpo: bytes, *, message_id: str = "mensagem-do-teste") -> None:
        channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=corpo,
            properties=pika.BasicProperties(message_id=message_id, delivery_mode=2),
            mandatory=True,
        )

    yield _publicar
    connection.close()


@pytest.fixture
def novo_lote():
    """Fábrica de envelopes válidos do contrato: ``novo_lote(registros=3)``.

    O bruto de cada registro tem o ``id`` dele e uma leitura de ``rpm``
    disponível. As datas têm milissegundos, a precisão da data BSON.
    """

    def _novo(registros: int = 1, *, sender_id: int = 51) -> TelemetryBatchV1:
        agora = datetime.now(UTC).replace(microsecond=123000)
        ids = [uuid4() for _ in range(registros)]
        return TelemetryBatchV1(
            message_id=uuid4(),
            received_at=agora,
            request_id="req-do-teste",
            sender=Sender(user_id=sender_id),
            records=tuple(
                TelemetryRecordV1(
                    record_id=record_id,
                    vin="93YBSR7RHEJ298116",
                    vehicle_id=7,
                    sender_relation="owner",
                    collected_at=agora,
                    payload_user_matches_sender=True,
                    raw={
                        "id": str(record_id),
                        "readings": [
                            {"metric": "rpm", "unit": "rpm", "available": True, "value": 800.0}
                        ],
                    },
                )
                for record_id in ids
            ),
        )

    return _novo


class FilaEmMemoria:
    """Dublê do ``TelemetryPublisher``: guarda os lotes publicados, ou falha como um
    broker fora do ar quando ``falhar`` é verdadeiro."""

    def __init__(self) -> None:
        self.publicados: list = []
        self.falhar = False

    def publish(self, batch) -> None:
        if self.falhar:
            raise PublishFailed("broker fora do ar (dublê)")
        self.publicados.append(batch)


@pytest.fixture
def fila_em_memoria():
    """Um ``FilaEmMemoria`` novo. Para a aplicação usá-lo, veja a fixture ``fila``
    de ``tests/test_telemetry.py``."""
    return FilaEmMemoria()


class LeituraEmMemoria:
    """Dublê do ``BronzeReader``, com a semântica do adaptador: a ordem
    ``(ingested_at, record_id)``, o começo estritamente depois da posição, o limite
    superior inclusivo, o escopo de dono e os filtros.

    ``agora`` é a hora do servidor (com milissegundos, como a data BSON); ``gravar``
    guarda um registro gravado há tantos segundos; ``fora`` faz o bronze falhar como
    o Mongo fora do ar; ``chamadas`` guarda os argumentos de cada leitura.
    """

    def __init__(self) -> None:
        self.agora = datetime.now(UTC).replace(microsecond=482000)
        self.registros: list[BronzeEntry] = []
        self.chamadas: list[dict] = []
        self.fora = False

    def gravar(
        self,
        ingerido_ha: float,
        *,
        user_id: int = 51,
        vin: str = "93YBSR7RHEJ298116",
        metricas: tuple[str, ...] = ("rpm",),
    ) -> BronzeEntry:
        ingested_at = self.agora - timedelta(seconds=ingerido_ha)
        record_id = uuid4()
        registro = BronzeEntry(
            record_id=record_id,
            vin=vin,
            vehicle_id=7,
            user_id=user_id,
            sender_relation="owner",
            payload_user_matches_sender=True,
            collected_at=ingested_at - timedelta(minutes=2),
            available_metrics=metricas,
            received_at=ingested_at - timedelta(seconds=1),
            ingested_at=ingested_at,
            message_id=uuid4(),
            request_id="req-do-teste",
            schema_version=1,
            raw={"id": str(record_id), "readings": [{"metric": "rpm", "value": 1065.0}]},
        )
        self.registros.append(registro)
        return registro

    def server_time(self) -> datetime:
        if self.fora:
            raise BronzeUnavailable("Mongo fora do ar (dublê)")
        return self.agora

    def read_after(self, after, *, until, limit, owner_scope, filters) -> list[BronzeEntry]:
        if self.fora:
            raise BronzeUnavailable("Mongo fora do ar (dublê)")
        self.chamadas.append(
            {
                "after": after,
                "until": until,
                "limit": limit,
                "dono": owner_scope,
                "filtros": filters,
            }
        )

        def ordem(registro: BronzeEntry):
            return (registro.ingested_at, str(registro.record_id))

        depois = [
            registro
            for registro in self.registros
            if ordem(registro) > (after.ingested_at, str(after.record_id))
            and registro.ingested_at <= until
            and (owner_scope is ANY_OWNER or registro.user_id == owner_scope)
            and (filters.vin is None or registro.vin == filters.vin)
            and (not filters.metrics or set(filters.metrics) & set(registro.available_metrics))
            and (filters.collected_from is None or registro.collected_at >= filters.collected_from)
            and (filters.collected_to is None or registro.collected_at <= filters.collected_to)
        ]
        return sorted(depois, key=ordem)[:limit]


@pytest.fixture
def leitura_em_memoria():
    """Um ``LeituraEmMemoria`` novo, sem registros. Para a aplicação usá-lo, veja a
    fixture ``bronze`` de ``tests/test_bronze_api.py``."""
    return LeituraEmMemoria()
