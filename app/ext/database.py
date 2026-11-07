"""Instância do SQLAlchemy e registro dos modelos."""

from importlib import import_module

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from domain.base import BaseModel

db = SQLAlchemy(model_class=BaseModel)


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
