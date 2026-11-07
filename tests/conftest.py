"""Fixtures da suíte.

Os testes rodam contra PostgreSQL de verdade (o mesmo dialeto de produção), e
não SQLite: índices parciais, `ilike`, TIMESTAMPTZ e defaults de servidor se
comportam de forma diferente entre dialetos, e justamente esses detalhes é que
precisam de cobertura.

Configure o banco de testes em `FLASK_TEST_DATABASE_URI` (ver .env.example).
"""

import os

import pytest
from sqlalchemy import event

from authz import Role
from ext.config import build_config

DEFAULT_TEST_DATABASE_URI = "postgresql+psycopg://baseapi:baseapi@localhost:5432/baseapi_test"
DEFAULT_PASSWORD = "senha-de-teste-123"

# Carrega settings.toml + .env + variáveis de ambiente sem montar a aplicação —
# é a mesma função que o Alembic usa. Reaproveitá-la evita manter aqui um parser
# de .env escrito à mão.
_config = build_config()

# Atribuição (e não setdefault): mesmo que o .env aponte para o banco de
# desenvolvimento, a suíte precisa ir para o banco de testes — ela apaga tabelas.
os.environ["FLASK_SQLALCHEMY_DATABASE_URI"] = (
    _config.get("TEST_DATABASE_URI") or DEFAULT_TEST_DATABASE_URI
)

# Chaves fixas e descartáveis. Precisam ter 32 bytes ou mais: abaixo disso o
# PyJWT avisa que a chave é curta demais para HMAC-SHA256 (RFC 7518).
os.environ.setdefault("FLASK_SECRET_KEY", "chave-de-teste-sem-nenhum-valor-real-0001")
os.environ.setdefault("FLASK_JWT_SECRET_KEY", "chave-jwt-de-teste-sem-valor-real-0002")

from app import create_app  # noqa: E402
from ext.database import db  # noqa: E402


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
