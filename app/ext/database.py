"""Instância do SQLAlchemy, registro dos modelos e a sonda do banco."""

import logging
from importlib import import_module

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from domain.base import BaseModel
from instrumentation.probes import register_probe

logger = logging.getLogger(__name__)

db = SQLAlchemy(model_class=BaseModel)


def _database_responds() -> bool:
    """Sonda do health: o Postgres responde a um ``SELECT 1``.

    Processo no ar sem banco não serve requisição nenhuma, então a sonda
    verifica a conexão de verdade, e não só o processo.
    """
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        # A sessão fica inutilizável depois da falha: reverter antes de responder.
        db.session.rollback()
        logger.warning("Sonda do banco: sem resposta (%s: %.300s)", type(error).__name__, error)
        return False
    return True


def init_app(app: Flask) -> None:
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError(
            "SQLALCHEMY_DATABASE_URI não definida. Copie .env.example para .env e "
            "preencha FLASK_SQLALCHEMY_DATABASE_URI (ou rode via docker compose, "
            "que monta a URI automaticamente)."
        )

    db.init_app(app)

    # Importar o módulo de modelos é o que registra as tabelas no metadata.
    # Sem isso, create_all() e o autogenerate do Alembic não veem nada.
    import_module("domain.models")

    register_probe(app, "database", _database_responds)
