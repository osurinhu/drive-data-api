"""Configuração e carregamento dinâmico de módulos.

Em vez de imports fixos, as duas listas do settings.toml (``EXTENSIONS`` e
``API_V1_ROUTES``) guardam caminhos de módulo importados na inicialização.
Estender a aplicação passa a ser aditivo: uma linha nova na configuração.
"""

from importlib import import_module
from pathlib import Path

from dynaconf import FlaskDynaconf
from flask import Flask
from flask.config import Config
from flask_smorest import Api, Blueprint

# Caminho absoluto, para que a descoberta do settings.toml não dependa do
# diretório de trabalho.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def init_app(app: Flask) -> None:
    """Carrega a configuração. Precedência (maior primeiro): variável de
    ambiente ``FLASK_*`` > .env > settings.toml — é assim que os segredos ficam
    fora do controle de versão."""
    FlaskDynaconf(
        app,
        envvar_prefix="FLASK",
        settings_file=[str(PROJECT_ROOT / "settings.toml")],
        load_dotenv=True,
        dotenv_path=str(PROJECT_ROOT / ".env"),
    )


def build_config() -> Config:
    """Carrega só a configuração, sem montar a aplicação.

    Usado pelas migrações e pela suíte: aplicar um `alembic upgrade` precisa
    apenas da URL do banco, não de JWT, CORS e rotas inicializados.
    """
    probe = Flask("config-probe")
    init_app(probe)
    return probe.config


def load_extensions(app: Flask) -> None:
    """Importa cada módulo de ``EXTENSIONS`` e chama seu ``init_app(app)``."""
    for module_path in app.config.get("EXTENSIONS", []):
        module = import_module(module_path)
        module.init_app(app)


def load_routes(app: Flask, api: Api, parent: Blueprint, config_key: str) -> None:
    """Registra os blueprints de ``config_key`` sob um blueprint pai versionado
    (ex.: ``/api/v1``), para que a versão da API fique declarada num lugar só."""
    for module_path in app.config.get(config_key, []):
        module = import_module(module_path)
        parent.register_blueprint(module.blp)

    api.register_blueprint(parent)
