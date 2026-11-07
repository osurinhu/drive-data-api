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

import os

import pytest
from flask import Flask
from sqlalchemy import event

from authz import Role
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
from ext import mongo as ext_mongo  # noqa: E402
from ext.database import db  # noqa: E402
from ext.mongo import mongo  # noqa: E402
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
    """O banco de testes do Mongo (``<MONGO_DATABASE>_test``), com o contexto de
    aplicação aberto durante o teste, como os adaptadores de ``repository/mongo``
    exigem."""
    with _app_do_mongo.app_context():
        yield mongo.db


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
