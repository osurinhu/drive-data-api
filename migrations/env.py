"""Ambiente do Alembic.

Carrega apenas a *configuração* da aplicação — não a aplicação inteira. Assim
rodar uma migração não exige que segredos de JWT, CORS e afins estejam
definidos: migrar o banco só precisa da URL do banco.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# `app/` é a raiz de imports da aplicação (mesma convenção do runtime).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))

import domain.models  # noqa: E402, F401  (importar registra as tabelas no metadata)
from domain.base import BaseModel  # noqa: E402
from ext.config import build_config  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BaseModel.metadata

app_config = build_config()
database_url = app_config.get("SQLALCHEMY_DATABASE_URI")
if not database_url:
    raise SystemExit(
        "SQLALCHEMY_DATABASE_URI não definida. Defina FLASK_SQLALCHEMY_DATABASE_URI "
        "no ambiente (ou no .env) antes de rodar as migrações."
    )


def run_migrations_offline() -> None:
    """Gera o SQL das migrações sem conectar ao banco (`alembic upgrade --sql`)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Aplica as migrações conectando ao banco."""
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Detecta mudança de tipo de coluna no autogenerate.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
