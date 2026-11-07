"""CORS — quais origens podem chamar a API pelo navegador."""

from flask import Flask
from flask_cors import CORS

cors = CORS()


def init_app(app: Flask) -> None:
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config.get("CORS_ORIGINS", [])}},
    )
