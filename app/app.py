"""Ponto de entrada da aplicação.

O factory não conhece nenhuma extensão: quem decide o que a aplicação tem é a
lista EXTENSIONS do settings.toml. Acrescentar funcionalidade nunca exige editar
este arquivo.
"""

from flask import Flask

from ext import config


def create_app() -> Flask:
    app = Flask(__name__)

    config.init_app(app)
    config.load_extensions(app)

    return app
